"""n8n webhook client: dry run, one pooled client per event loop, failures never raise."""
import asyncio

import httpx
import pytest

from app.services import alert_service

URL = "http://n8n.test/webhook/hospital-alerts"
ALERT = {"key": "inventory:M1", "type": "inventory", "severity": "high", "subject": "AMOXICILINA",
         "message": "m", "recommendation": "r", "data": {}}


@pytest.fixture
def n8n(monkeypatch):
    """Real sending (dry run off) through an in-memory transport that records the requests."""
    sent: list[httpx.Request] = []
    behaviour = {"raise": None}

    def handler(request: httpx.Request) -> httpx.Response:
        if behaviour["raise"]:
            raise behaviour["raise"]
        sent.append(request)
        return httpx.Response(200, json={"ok": True})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(alert_service.httpx, "AsyncClient",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(alert_service.settings, "N8N_WEBHOOK_URL", URL)
    monkeypatch.setattr(alert_service.settings, "N8N_DRY_RUN", False)
    return sent, behaviour


async def test_dry_run_simulates_success_without_network(monkeypatch):
    monkeypatch.setattr(alert_service.settings, "N8N_WEBHOOK_URL", URL)
    assert alert_service.settings.N8N_DRY_RUN is True  # enabled for the whole suite (conftest)
    assert await alert_service.send_to_n8n(ALERT, "es") is None
    assert alert_service._http is None  # no client, no socket


async def test_webhook_is_sent(n8n):
    sent, _ = n8n
    assert await alert_service.send_to_n8n(ALERT, "es") is None
    assert sent[0].url == URL and b'"event":"hospital_alert"' in sent[0].content.replace(b" ", b"")


def test_one_client_per_event_loop(n8n):
    """The original bug: a client created in one loop reused from the next one."""
    sent, _ = n8n
    clients = []

    async def send():
        assert await alert_service.send_to_n8n(ALERT, "es") is None
        clients.append(alert_service._http)

    asyncio.run(send())  # first loop: creates a client, then the loop is closed
    asyncio.run(send())  # second loop: must NOT reuse the first client
    assert len(sent) == 2 and clients[0] is not clients[1]
    asyncio.run(alert_service.close_http_client())  # safe from yet another loop
    assert alert_service._http is None


@pytest.mark.parametrize("error", [httpx.ConnectError("connection refused"),
                                   RuntimeError("Event loop is closed")])
async def test_failures_are_returned_not_raised(n8n, error):
    _, behaviour = n8n
    behaviour["raise"] = error
    result = await alert_service.send_to_n8n(ALERT, "es")
    assert result.startswith(type(error).__name__)  # an error, never a fake success


async def test_failed_notification_is_recorded(n8n, db):
    _, behaviour = n8n
    behaviour["raise"] = httpx.ConnectError("n8n down")
    summary = await alert_service.evaluate_and_notify(db)
    assert summary["new"] >= 1  # evaluation completed despite the outage
    doc = await db["alerts"].find_one({"_id": "inventory:M1"})
    assert doc["notified_at"] is None and "n8n down" in doc["notify_error"]


async def test_close_is_idempotent():
    await alert_service.close_http_client()
    await alert_service.close_http_client()
