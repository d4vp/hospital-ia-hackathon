"""Optional billing module (admin-only). Mounted only when BILLING_ENABLED is true.

GET /api/billing/summary?start=&end=&insurer=   billable volume (and estimated amounts when
                                                tariffs are configured) by insurer, regime,
                                                month and item
"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import AdminUser, get_db
from app.services import billing_service
from app.services.data_repository import get_frames

router = APIRouter(prefix="/billing", tags=["billing"])


@router.get("/summary")
async def billing_summary(
    _: AdminUser,
    db=Depends(get_db),
    start: Optional[date] = None,
    end: Optional[date] = None,
    insurer: Optional[str] = Query(default=None, max_length=160),
) -> dict:
    return await billing_service.billing_summary(db, await get_frames(db), start, end, insurer)
