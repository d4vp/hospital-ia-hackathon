"""GET /api/reports/inferential (alias /api/reports/inferencial)."""
from fastapi import APIRouter, Depends
from fastapi.concurrency import run_in_threadpool

from app.api.deps import CurrentUser, Language, get_db
from app.services.data_repository import get_frames
from app.services.report_service import build_inferential_report

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/inferential")
@router.get("/inferencial", include_in_schema=False)
async def inferential_report(_: CurrentUser, lang: Language, db=Depends(get_db)) -> dict:
    frames = await get_frames(db)
    return await run_in_threadpool(build_inferential_report, frames, lang)
