"""Alerts and their attention workflow.

GET   /api/alerts                active alerts (finalized ones are excluded)
GET   /api/alerts/summary        counts by severity / status + one short line per alert (ticker)
GET   /api/alerts/history        closed alerts: finalized by staff or resolved by the data
GET   /api/alerts/events         state history: every status transition (who, when, from -> to)
PATCH /api/alerts/{key}          workflow transition: reviewed | in_progress | finalized
POST  /api/alerts/evaluate       admin: re-evaluate the rules and notify n8n
"""
from typing import Literal, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import Field

from app.api.deps import AdminUser, CurrentUser, Language, get_db
from app.api.models import StrictModel
from app.core.config import settings
from app.services import alert_service
from app.services.alert_service import WorkflowStatus

router = APIRouter(prefix="/alerts", tags=["alerts"])

Severity = Literal["critical", "high", "medium"]


class StatusChange(StrictModel):
    status: Literal["reviewed", "in_progress", "finalized"]
    note: str = Field(default="", max_length=500)


@router.get("")
async def list_alerts(_: CurrentUser, lang: Language, db=Depends(get_db)) -> list[dict]:
    return await alert_service.active_alerts(db, lang)


@router.get("/summary")
async def summary(_: CurrentUser, lang: Language, db=Depends(get_db)) -> dict:
    return await alert_service.alerts_summary(db, lang)


@router.get("/history")
async def history(
    _: CurrentUser,
    lang: Language,
    db=Depends(get_db),
    limit: int = Query(default=100, ge=1, le=500),
    severity: Optional[Severity] = None,
    reason: Optional[Literal["finalized", "resolved"]] = None,
    alert_type: Optional[str] = Query(default=None, alias="type", max_length=40, pattern=r"^[a-z_]+$"),
) -> list[dict]:
    return await alert_service.alert_history(db, lang, limit, severity, reason, alert_type)


@router.get("/events")
async def events(
    _: CurrentUser,
    db=Depends(get_db),
    limit: int = Query(default=200, ge=1, le=1000),
    key: Optional[str] = Query(default=None, max_length=200),
) -> list[dict]:
    return await alert_service.alert_events(db, limit, key)


@router.post("/evaluate")
async def evaluate(_: AdminUser, db=Depends(get_db)) -> dict:
    return await alert_service.evaluate_and_notify(db)


# Declared last: `{key:path}` accepts keys such as "occupancy:UNIDAD DE CUIDADO INTENSIVO".
@router.patch("/{key:path}")
async def change_status(key: str, body: StatusChange, user: CurrentUser, background: BackgroundTasks,
                        lang: Language, db=Depends(get_db)) -> dict:
    if not key or len(key) > 200:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    actor = user.get("full_name") or user["email"]
    new_status = WorkflowStatus(body.status)
    try:
        doc = await alert_service.change_status(db, key, new_status, actor, body.note)
    except alert_service.AlertNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except alert_service.InvalidTransitionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    # The webhook runs after the response is sent: the UI never waits for n8n.
    background.add_task(alert_service.notify_status_change, doc, new_status, actor, body.note,
                        settings.ALERT_LANGUAGE)
    return alert_service.public_alert(doc, lang)
