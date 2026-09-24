"""Attending an alert: it stays visible as "in progress", stops counting as pending, is logged."""
from app.services import alert_service
from app.services.alert_service import WorkflowStatus

KEY = "inventory:M1"


async def test_attending_lowers_pending_but_keeps_the_alert(db):
    await alert_service.evaluate_and_notify(db)
    before = await alert_service.alerts_summary(db, "es")
    assert before["pending_by_severity"]["high"] == 1 and before["in_progress"] == 0

    await alert_service.change_status(db, KEY, WorkflowStatus.IN_PROGRESS, "Ana", "en camino")
    after = await alert_service.alerts_summary(db, "es")
    assert after["pending_by_severity"]["high"] == 0 and after["in_progress"] == 1
    assert after["total"] == before["total"]  # nothing was removed
    active = {a["key"]: a for a in await alert_service.active_alerts(db, "es")}
    assert active[KEY]["workflow_status"] == "in_progress" and active[KEY]["pending"] is False
    assert active[KEY]["workflow_log"][0]["note"] == "en camino"
    assert after["items"][-1]["key"] == KEY  # attended alerts go after the pending ones


async def test_status_history_is_kept(db, user_client):
    await alert_service.evaluate_and_notify(db)
    await user_client.patch("/alerts/inventory%3AM1", json={"status": "reviewed"})
    await user_client.patch("/alerts/inventory%3AM1", json={"status": "in_progress", "note": "ok"})
    events = (await user_client.get("/alerts/events")).json()
    assert [(e["from_status"], e["to_status"]) for e in events] == [("reviewed", "in_progress"), ("new", "reviewed")]
    assert events[0]["by"] == "User" and events[0]["note"] == "ok"
    only_key = (await user_client.get("/alerts/events", params={"key": KEY, "limit": 1})).json()
    assert len(only_key) == 1
    summary = (await user_client.get("/alerts/summary")).json()
    assert summary["by_status"]["in_progress"] == 1
