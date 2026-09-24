"""On-demand inferential reports.

GET /api/reports/sections                       catalogue of the available sections
GET /api/reports/inferential?sections=a&sections=b
    (alias /api/reports/inferencial)            computes ONLY the requested sections
"""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import CurrentUser, Language, get_db
from app.services.data_repository import get_frames
from app.services.report_service import SECTIONS, UnknownSectionError, build_report_on_demand

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/sections")
async def report_sections(_: CurrentUser) -> list[str]:
    return list(SECTIONS)


@router.get("/inferential")
@router.get("/inferencial", include_in_schema=False)
async def inferential_report(
    _: CurrentUser,
    lang: Language,
    sections: Annotated[list[str], Query(max_length=len(SECTIONS) * 2, description="Sections to compute")] = [],  # noqa: B006
    db=Depends(get_db),
) -> dict:
    try:
        return await build_report_on_demand(await get_frames(db), lang, sections)
    except UnknownSectionError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
