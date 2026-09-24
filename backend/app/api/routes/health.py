"""GET /api/health (public)."""
from fastapi import APIRouter

from app.core.config import settings
from app.db.mongo import ping

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "mongo": await ping(),
        "llm_enabled": settings.openai_enabled,
        "features": {"billing": settings.BILLING_ENABLED, "readonly_db_user": bool(settings.MONGO_READONLY_URI.strip())},
    }
