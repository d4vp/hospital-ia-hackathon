"""Clinical record insertion (admin-only). SQL Server first, then MongoDB (see records.service).

GET  /api/records/catalog          allowed values for the forms + mode (sqlserver | mongo_only)
POST /api/records/admissions       new admission (and new patient, if needed)
POST /api/records/services         service provided to an admission
POST /api/records/medications      medication / supply dispensed to an admission
GET  /api/records/sync             outbox status (records waiting to reach MongoDB)
POST /api/records/sync/retry       re-project queued records now
"""
from typing import Any, Awaitable, Callable

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import AdminUser, get_db
from app.services.records import service
from app.services.records.models import AdmissionIn, MedicationLineIn, ServiceLineIn
from app.services.records.stores import StoreIntegrityError, StoreUnavailableError

router = APIRouter(prefix="/records", tags=["records"])

# Domain error -> HTTP status (checked in order)
ERRORS: tuple[tuple[type[Exception], int], ...] = (
    (service.RecordValidationError, status.HTTP_422_UNPROCESSABLE_ENTITY),
    (StoreIntegrityError, status.HTTP_409_CONFLICT),
    (StoreUnavailableError, status.HTTP_503_SERVICE_UNAVAILABLE),
)


async def _guarded(call: Callable[[], Awaitable[dict[str, Any]]]) -> dict[str, Any]:
    try:
        return await call()
    except tuple(error for error, _ in ERRORS) as exc:
        code = next(code for error, code in ERRORS if isinstance(exc, error))
        raise HTTPException(status_code=code, detail=str(exc))


def _actor(user: dict) -> str:
    return user.get("email") or str(user["_id"])


@router.get("/catalog")
async def catalog(_: AdminUser, db=Depends(get_db)) -> dict:
    return await service.catalog(db)


@router.post("/admissions", status_code=status.HTTP_201_CREATED)
async def create_admission(body: AdmissionIn, admin: AdminUser, db=Depends(get_db)) -> dict:
    return await _guarded(lambda: service.create_admission(db, body, _actor(admin)))


@router.post("/services", status_code=status.HTTP_201_CREATED)
async def create_service(body: ServiceLineIn, admin: AdminUser, db=Depends(get_db)) -> dict:
    return await _guarded(lambda: service.create_service(db, body, _actor(admin)))


@router.post("/medications", status_code=status.HTTP_201_CREATED)
async def create_medication(body: MedicationLineIn, admin: AdminUser, db=Depends(get_db)) -> dict:
    return await _guarded(lambda: service.create_medication(db, body, _actor(admin)))


@router.get("/sync")
async def sync_status(_: AdminUser, db=Depends(get_db)) -> dict:
    return await service.sync_status(db)


@router.post("/sync/retry")
async def retry(_: AdminUser, db=Depends(get_db)) -> dict:
    return await service.retry_pending(db)
