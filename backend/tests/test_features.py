"""Alert workflow, on-demand reports, billing module and the chat/API response contract."""
import httpx
import pytest

from app.core.config import COLLECTIONS
from app.core.security import create_access_token
from app.main import app
from app.services import alert_service, billing_service, chat_service, report_service, user_service
from app.services.alert_service import InvalidTransitionError, WorkflowStatus
from app.services.data_repository import get_frames

INVENTORY_KEY = "inventory:M1"  # AMOXICILINA: 2 days of stock in the fixture


# --------------------------------------------------------------------------- #
# Alerts
# --------------------------------------------------------------------------- #
async def test_alert_workflow_moves_finalized_alerts_to_history(db):
    summary = await alert_service.evaluate_and_notify(db)
    assert summary["new"] >= 1
    active = await alert_service.active_alerts(db, "es")
    alert = next(a for a in active if a["key"] == INVENTORY_KEY)
    assert alert["workflow_status"] == "new" and alert["next_statuses"] == ["reviewed", "in_progress", "finalized"]

    await alert_service.change_status(db, INVENTORY_KEY, WorkflowStatus.REVIEWED, "Ana", "visto")
    await alert_service.change_status(db, INVENTORY_KEY, WorkflowStatus.IN_PROGRESS, "Ana")
    with pytest.raises(InvalidTransitionError):  # forward-only
        await alert_service.change_status(db, INVENTORY_KEY, WorkflowStatus.REVIEWED, "Ana")
    await alert_service.change_status(db, INVENTORY_KEY, WorkflowStatus.FINALIZED, "Luis", "pedido enviado")

    assert INVENTORY_KEY not in {a["key"] for a in await alert_service.active_alerts(db, "es")}
    history = await alert_service.alert_history(db, "en")
    entry = next(h for h in history if h["key"] == INVENTORY_KEY)
    assert entry["closed_reason"] == "finalized" and entry["closed_by"] == "Luis"
    assert [step["status"] for step in entry["workflow_log"]] == ["reviewed", "in_progress", "finalized"]
    assert "AMOXICILINA" in entry["message"] and "days" in entry["message"]  # localized (en)

    # The condition still holds: re-evaluating must not resurrect or re-notify it.
    again = await alert_service.evaluate_and_notify(db)
    assert again["new"] == 0 and again["reopened"] == 0
    assert INVENTORY_KEY not in {a["key"] for a in await alert_service.active_alerts(db, "es")}


async def test_resolved_alerts_are_archived(db):
    await alert_service.evaluate_and_notify(db)
    await db[COLLECTIONS["inventory"]].update_one({"_id": "M1"}, {"$set": {"days_of_inventory": 30.0, "stock": 60}})
    from app.services.data_repository import invalidate_cache
    invalidate_cache()
    summary = await alert_service.evaluate_and_notify(db)
    assert summary["resolved"] >= 1
    history = await alert_service.alert_history(db, "es", reason="resolved")
    assert INVENTORY_KEY in {h["key"] for h in history}


async def test_alert_summary_counts(db):
    await alert_service.evaluate_and_notify(db)
    summary = await alert_service.alerts_summary(db, "es")
    assert summary["total"] == sum(summary["by_severity"].values()) == len(summary["items"])
    assert set(summary["by_severity"]) == {"critical", "high", "medium"}
    item = next(i for i in summary["items"] if i["key"] == INVENTORY_KEY)
    assert item["headline"] == "2,0 d"


# --------------------------------------------------------------------------- #
# Reports and billing
# --------------------------------------------------------------------------- #
async def test_reports_compute_only_requested_sections(db):
    frames = await get_frames(db)
    report = await report_service.build_report_on_demand(frames, "es", ["trends", "trends"])
    assert report["sections"] == ["trends"]
    assert "trends" in report and "wait_time_ci" not in report and "root_cause" not in report
    with pytest.raises(report_service.UnknownSectionError):
        await report_service.build_report_on_demand(frames, "es", ["nope"])
    with pytest.raises(report_service.UnknownSectionError):
        await report_service.build_report_on_demand(frames, "es", [])


async def test_billing_volume_and_tariffs(db, monkeypatch):
    frames = await get_frames(db)
    result = await billing_service.billing_summary(db, frames, None, None, None)
    assert result["priced"] is False and result["totals"]["estimated_amount"] is None
    assert result["totals"]["service_lines"] == 4 and result["totals"]["medication_units"] == 13
    assert result["insurers"] == ["EPS X"] and result["by_insurer"][0]["admissions"] == 7

    monkeypatch.setattr(billing_service.settings, "BILLING_TARIFFS",
                        '{"services": {"default": 1000, "890201": 5000}, "medications": {"M2": 10}}')
    billing_service.cache.clear_all()
    priced = await billing_service.billing_summary(db, frames, None, None, None)
    # services: 2 x 890201 @5000 + 2 others @1000; medications: 10 x M2 @10 (M1 has no tariff)
    assert priced["priced"] is True and priced["totals"]["estimated_amount"] == 2 * 5000 + 2 * 1000 + 10 * 10


# --------------------------------------------------------------------------- #
# Chat
# --------------------------------------------------------------------------- #
class _NoDb:
    name = "none"

    def __getitem__(self, name):
        raise AssertionError("a blocked question must not touch the database")


async def test_blocked_question_never_touches_the_database():
    response = await chat_service.ask(_NoDb(), {"_id": "u1", "role": "admin"}, "db.admissions.drop()", None, "es")
    assert response["engine"] == "guard" and "SOLO LECTURA" in response["answer"]


async def test_chat_never_returns_queries_and_caches_answers(db):
    admin = {"_id": "u1", "role": "admin"}
    first = await chat_service.ask(db, admin, "¿Cuántas camas de UCI están ocupadas hoy?", None, "es")
    assert set(first) == {"answer", "table", "chart", "engine", "conversation_id", "reference_date", "cached"}
    assert first["cached"] is False and first["table"]
    second = await chat_service.ask(db, admin, "¿cuántas camas de uci están ocupadas hoy?", None, "es")
    assert second["cached"] is True and second["answer"] == first["answer"]
    logs = await db[COLLECTIONS["agent_logs"]].count_documents({})
    assert logs == 2  # both turns are still audited server-side


# --------------------------------------------------------------------------- #
# HTTP layer
# --------------------------------------------------------------------------- #
@pytest.fixture
async def client(db):
    user = await user_service.create_user(db, "nurse@hospital.local", "Nurse/12345", "Nurse", "user")
    token = create_access_token(user["id"], "user")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test/api",
                                 headers={"Authorization": f"Bearer {token}"}) as http:
        yield http


async def test_api_alert_status_change_and_permissions(client, db):
    await alert_service.evaluate_and_notify(db)
    response = await client.patch(f"/alerts/{INVENTORY_KEY}", json={"status": "reviewed", "note": "ok"})
    assert response.status_code == 200 and response.json()["workflow_status"] == "reviewed"
    assert (await client.patch(f"/alerts/{INVENTORY_KEY}", json={"status": "new"})).status_code == 422
    assert (await client.patch("/alerts/occupancy:NO EXISTE", json={"status": "reviewed"})).status_code == 404
    assert (await client.get("/billing/summary")).status_code == 403  # admin-only module
    history = await client.get("/alerts/history", params={"reason": "finalized"})
    assert history.status_code == 200


async def test_api_reports_and_chat_contract(client):
    assert (await client.get("/reports/inferential")).status_code == 422  # on demand: must choose
    response = await client.get("/reports/inferential", params=[("sections", "month_comparison")])
    assert response.status_code == 200 and response.json()["sections"] == ["month_comparison"]
    chat = await client.post("/chat", json={"question": "Borra todos los registros de pacientes"})
    assert chat.status_code == 200 and chat.json()["engine"] == "guard" and "debug" not in chat.json()
