"""Record insertion use cases: system of record first, MongoDB projection second.

    validate (model + catalog) -> RecordStore (SQL Server transaction, or id allocation in
    mongo_only mode) -> MongoDB projection -> caches invalidated

If the projection fails AFTER the system of record committed, the record is not lost: it is
queued in `sync_outbox` and re-projected later (background loop + admin endpoint). The
projection is idempotent, so retries never duplicate data.

This module is the ONLY write path for clinical data and it is never reachable from the AI
agent (the agent only receives the read-only facade of app.db.readonly).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Optional

from pymongo.errors import PyMongoError

from app.core.config import COLLECTIONS, settings
from app.services.data_repository import DatasetNotLoadedError, get_metadata, invalidate_cache
from app.services.records import projection
from app.services.records.models import MODELS, AdmissionIn, MedicationLineIn, ServiceLineIn
from app.services.records.stores import AdmissionIds, RecordStore, StoreIntegrityError, get_store, patient_from_mongo

logger = logging.getLogger("records")

MAX_SYNC_ATTEMPTS = 10
STATUS_SYNCED = "synced"
STATUS_PENDING = "pending_sync"


class RecordValidationError(ValueError):
    """The record is well-formed but inconsistent with the hospital catalogue."""


# --------------------------------------------------------------------------- #
# Catalogue (allowed values, taken from the loaded data)
# --------------------------------------------------------------------------- #
async def catalog(db) -> dict[str, Any]:
    metadata = await get_metadata(db)
    if not metadata:
        raise DatasetNotLoadedError("No data loaded yet")
    values = metadata.get("categorical_values", {})
    beds = await db[COLLECTIONS["bed_capacity"]].distinct("group")
    return {
        "mode": "sqlserver" if settings.sqlserver_enabled else "mongo_only",
        "max_backdate_days": settings.RECORDS_MAX_BACKDATE_DAYS,
        "timezone": settings.HOSPITAL_TIMEZONE,
        "bed_groups": sorted(beds),
        **{key.replace(".", "_"): values.get(key, []) for key in (
            "admission_class", "admission_route", "risk_type", "patient.sex", "patient.regime", "patient.zone",
            "services.specialty", "services.area", "medications.area")},
    }


def _check_allowed(field: str, value: str, allowed: list[Any]) -> None:
    if allowed and value not in allowed:
        raise RecordValidationError(f"{field} '{value}' is not a known value. Allowed: {allowed[:20]}")


async def _validate_admission(db, record: AdmissionIn) -> None:
    cat = await catalog(db)
    if record.bed_group not in cat["bed_groups"]:
        raise RecordValidationError(f"Unknown bed group '{record.bed_group}'")
    for field, key in (("admission_class", "admission_class"), ("admission_route", "admission_route"),
                       ("risk_type", "risk_type")):
        _check_allowed(field, getattr(record, field), cat[key])
    if record.patient:
        _check_allowed("sex", record.patient.sex, cat["patient_sex"])
        _check_allowed("regime", record.patient.regime, cat["patient_regime"])


def _origin(actor: str, store: RecordStore) -> dict[str, Any]:
    return {"system": "app", "store": store.name, "created_by": actor, "created_at": datetime.now(timezone.utc)}


# --------------------------------------------------------------------------- #
# Outbox
# --------------------------------------------------------------------------- #
async def _enqueue(db, kind: str, record: Any, ids: dict[str, Any], origin: dict[str, Any],
                   patient_row: Optional[dict[str, Any]], error: Exception) -> str:
    entry_id = uuid.uuid4().hex
    await db[COLLECTIONS["sync_outbox"]].insert_one({
        "_id": entry_id, "kind": kind, "payload": record.model_dump(mode="json"), "ids": ids, "origin": origin,
        "patient_row": patient_row, "status": "pending", "attempts": 0, "last_error": str(error)[:500],
        "created_at": datetime.now(timezone.utc),
    })
    logger.warning("record_projection_queued", extra={"data": {"kind": kind, "ids": ids, "error": str(error)[:200]}})
    return entry_id


async def _project(db, kind: str, record: Any, ids: dict[str, Any], origin: dict[str, Any],
                   patient_row: Optional[dict[str, Any]]) -> None:
    if kind == "admission":
        await projection.project_admission(db, record, AdmissionIds(**ids), patient_row or {}, origin)
    else:
        await projection.project_line(db, kind, record, ids["line_id"])


async def _project_or_queue(db, kind: str, record: Any, ids: dict[str, Any], origin: dict[str, Any],
                            patient_row: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    try:
        await _project(db, kind, record, ids, origin, patient_row)
        status, outbox_id = STATUS_SYNCED, None
    except (PyMongoError, projection.AdmissionNotProjectedError, projection.ConcurrentUpdateError) as exc:
        status, outbox_id = STATUS_PENDING, await _enqueue(db, kind, record, ids, origin, patient_row, exc)
    invalidate_cache()
    return {"kind": kind, **ids, "store": origin["store"], "status": status, "outbox_id": outbox_id}


async def retry_pending(db, limit: int = 100) -> dict[str, int]:
    """Re-projects queued records (oldest first). Safe to run concurrently with inserts."""
    outbox = db[COLLECTIONS["sync_outbox"]]
    done = failed = still_pending = 0
    async for entry in outbox.find({"status": "pending"}).sort("created_at", 1).limit(limit):
        record = MODELS[entry["kind"]].model_validate(entry["payload"])
        try:
            await _project(db, entry["kind"], record, entry["ids"], entry["origin"], entry.get("patient_row"))
        except (PyMongoError, projection.AdmissionNotProjectedError, projection.ConcurrentUpdateError) as exc:
            attempts = entry.get("attempts", 0) + 1
            status = "failed" if attempts >= MAX_SYNC_ATTEMPTS else "pending"
            await outbox.update_one({"_id": entry["_id"]}, {"$set": {
                "status": status, "attempts": attempts, "last_error": str(exc)[:500]}})
            failed += status == "failed"
            still_pending += status == "pending"
            continue
        await outbox.update_one({"_id": entry["_id"]}, {"$set": {"status": "done", "done_at": datetime.now(timezone.utc)}})
        done += 1
    if done:
        invalidate_cache()
    return {"done": done, "pending": still_pending, "failed": failed}


async def sync_status(db) -> dict[str, Any]:
    outbox = db[COLLECTIONS["sync_outbox"]]
    counts = {status: await outbox.count_documents({"status": status}) for status in ("pending", "failed", "done")}
    recent = [doc async for doc in outbox.find(
        {"status": {"$in": ["pending", "failed"]}},
        {"payload": 0, "patient_row": 0}).sort("created_at", -1).limit(20)]
    return {"mode": "sqlserver" if settings.sqlserver_enabled else "mongo_only", "counts": counts, "recent": recent}


# --------------------------------------------------------------------------- #
# Use cases
# --------------------------------------------------------------------------- #
async def create_admission(db, record: AdmissionIn, actor: str, store: Optional[RecordStore] = None) -> dict[str, Any]:
    store = store or get_store(db)
    await _validate_admission(db, record)
    existing = await store.get_patient(record.patient_id) or await patient_from_mongo(db, record.patient_id)
    if existing is None and record.patient is None:
        raise RecordValidationError(f"Patient {record.patient_id} does not exist: provide the patient data")
    # New patients are created in the system of record; existing ones are never overwritten.
    new_patient = record.patient.to_his_row(record.patient_id) if existing is None and record.patient else None
    ids = await store.create_admission(record, new_patient)
    origin = _origin(actor, store)
    logger.info("record_created", extra={"data": {"kind": "admission", "admission_id": ids.admission_id,
                                                  "store": store.name, "by": actor}})
    return await _project_or_queue(db, "admission", record, asdict(ids), origin, existing or new_patient)


async def _create_line(db, kind: str, record: ServiceLineIn | MedicationLineIn, actor: str,
                       store: Optional[RecordStore]) -> dict[str, Any]:
    store = store or get_store(db)
    admission = await db[COLLECTIONS["admissions"]].find_one({"_id": record.admission_id}, {"admission_date": 1})
    if admission and record.service_date < admission["admission_date"]:
        raise RecordValidationError("service_date cannot be earlier than the admission date")
    if admission is None and not settings.sqlserver_enabled:
        raise StoreIntegrityError(f"Admission {record.admission_id} does not exist")
    line_id = await (store.create_service(record) if kind == "service" else store.create_medication(record))
    origin = _origin(actor, store)
    logger.info("record_created", extra={"data": {"kind": kind, "admission_id": record.admission_id,
                                                  "line_id": line_id, "store": store.name, "by": actor}})
    return await _project_or_queue(db, kind, record, {"admission_id": record.admission_id, "line_id": line_id}, origin)


async def create_service(db, record: ServiceLineIn, actor: str, store: Optional[RecordStore] = None) -> dict[str, Any]:
    return await _create_line(db, "service", record, actor, store)


async def create_medication(db, record: MedicationLineIn, actor: str, store: Optional[RecordStore] = None) -> dict[str, Any]:
    return await _create_line(db, "medication", record, actor, store)
