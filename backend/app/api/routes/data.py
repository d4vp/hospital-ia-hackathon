"""Admin-only data loading.

POST /api/load-data    re-runs the ETL on the server-side workbook (no client paths accepted)
POST /api/upload-data  uploads a new .xlsx (size-limited, signature-checked) and runs the ETL
GET  /api/data/status  dataset metadata (any authenticated user)
"""
import asyncio
import logging
import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool

from app.api.deps import AdminUser, CurrentUser, get_db
from app.core.config import settings
from app.services import alert_service
from app.services.data_loader import load_excel_to_mongo
from app.services.data_repository import get_metadata, invalidate_cache

router = APIRouter(tags=["data"])
logger = logging.getLogger("data")

_etl_lock = asyncio.Lock()
CHUNK = 1024 * 1024
XLSX_SIGNATURE = b"PK\x03\x04"


async def _run_etl(db) -> dict:
    if _etl_lock.locked():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A data load is already running")
    async with _etl_lock:
        try:
            summary = await run_in_threadpool(load_excel_to_mongo)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    invalidate_cache()
    summary["alerts"] = await alert_service.evaluate_and_notify(db)
    return summary


@router.post("/load-data")
async def load_data(admin: AdminUser, db=Depends(get_db)) -> dict:
    logger.info("load_data_requested", extra={"data": {"by": admin["email"]}})
    return await _run_etl(db)


@router.post("/upload-data")
async def upload_data(request: Request, admin: AdminUser, file: UploadFile = File(...), db=Depends(get_db)) -> dict:
    max_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    declared = int(request.headers.get("content-length") or 0)
    if declared > max_bytes + 1024 * 1024:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail=f"File exceeds {settings.MAX_UPLOAD_MB} MB")
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only .xlsx files are accepted")

    target = settings.excel_path
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, suffix=".part")
    size = 0
    try:
        with os.fdopen(fd, "wb") as out:
            first = True
            while chunk := await file.read(CHUNK):
                if first and not chunk.startswith(XLSX_SIGNATURE):
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The file is not a valid .xlsx")
                first = False
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                                        detail=f"File exceeds {settings.MAX_UPLOAD_MB} MB")
                out.write(chunk)
        os.replace(tmp_name, target)  # atomic: the ETL never reads a half-written file
    finally:
        if Path(tmp_name).exists():
            Path(tmp_name).unlink(missing_ok=True)
    logger.info("workbook_uploaded", extra={"data": {"by": admin["email"], "bytes": size}})
    summary = await _run_etl(db)
    summary["uploaded_mb"] = round(size / 1024 / 1024, 1)
    return summary


@router.get("/data/status")
async def data_status(_: CurrentUser, db=Depends(get_db)) -> dict:
    metadata = await get_metadata(db)
    if not metadata:
        return {"loaded": False}
    return {
        "loaded": True,
        "reference_date": metadata["reference_date"],
        "data_start": metadata.get("data_start"),
        "loaded_at": metadata.get("loaded_at"),
        "counts": metadata.get("counts", {}),
        "quality": metadata.get("quality", {}),
    }
