"""GET /api/kpis, GET /api/patients, GET /api/filters"""
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query

from app.api.deps import CurrentUser, get_db
from app.services import kpi_service
from app.services.data_repository import get_frames

router = APIRouter(tags=["kpis"])


@router.get("/filters")
async def filters(_: CurrentUser, db=Depends(get_db)) -> dict:
    return await kpi_service.cached_filter_options(await get_frames(db))


@router.get("/kpis")
async def kpis(
    _: CurrentUser,
    db=Depends(get_db),
    start: Optional[date] = None,
    end: Optional[date] = None,
    bed_group: Optional[str] = Query(default=None, max_length=80),
    specialty: Optional[str] = Query(default=None, max_length=120),
) -> dict:
    return await kpi_service.cached_dashboard(await get_frames(db), start, end, bed_group, specialty)


@router.get("/patients")
async def patients(
    _: CurrentUser,
    db=Depends(get_db),
    start: Optional[date] = None,
    end: Optional[date] = None,
    bed_group: Optional[str] = Query(default=None, max_length=80),
    specialty: Optional[str] = Query(default=None, max_length=120),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=500),
) -> dict:
    """De-identified patient list: no name, document, birth date, chief complaint or specific diagnosis."""
    return await kpi_service.cached_patients(await get_frames(db), start, end, bed_group, specialty, page, size)
