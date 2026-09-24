"""GET /api/alerts (any user), POST /api/alerts/evaluate (admin: re-evaluate and notify n8n)."""
from fastapi import APIRouter, Depends

from app.api.deps import AdminUser, CurrentUser, Language, get_db
from app.services import alert_service

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("")
async def list_alerts(_: CurrentUser, lang: Language, db=Depends(get_db)) -> list[dict]:
    return await alert_service.active_alerts(db, lang)


@router.post("/evaluate")
async def evaluate(_: AdminUser, db=Depends(get_db)) -> dict:
    return await alert_service.evaluate_and_notify(db)
