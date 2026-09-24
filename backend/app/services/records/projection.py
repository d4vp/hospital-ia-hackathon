"""Projects records written to the system of record into the MongoDB analytical model.

Structural integrity: documents are NOT hand-built. The same ETL `transform()` that loads the
workbook runs on the new rows (HIS columns, see data_loader.SHEET_COLUMNS), so an inserted
admission or line gets exactly the fields, types, text repairs and derived values
(ICD-10 chapter, triage level, wait minutes, shift, age group ...) of a loaded one.

Every step is idempotent, so a failed projection can simply be retried (outbox):
- admissions are written with `$setOnInsert` (never overwrite an existing document);
- lines are pushed only if their `line_id` is absent, guarded by an optimistic revision
  (`record_revision`) so concurrent inserts on the same admission cannot lose derived fields;
- daily aggregates are RECOMPUTED from the admissions for the affected day/key (not `$inc`);
- metadata counters are incremented only when the document / line was really added.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta
from typing import Any, Optional

import pandas as pd

from app.core.config import COLLECTIONS
from app.services.data_loader import ACTIVE_WINDOW, SHEET_COLUMNS, transform
from app.services.data_repository import DatasetNotLoadedError
from app.services.records.models import AdmissionIn, MedicationLineIn, ServiceLineIn
from app.services.records.stores import AdmissionIds

MAX_LINE_ATTEMPTS = 5
LINE_ARRAYS = {"service": "services", "medication": "medications"}


class AdmissionNotProjectedError(LookupError):
    """The parent admission is not in MongoDB yet (the line will be retried from the outbox)."""


class ConcurrentUpdateError(RuntimeError):
    """The admission kept changing while the line was being applied."""


def _sheets(rows: dict[str, list[dict[str, Any]]]) -> dict[str, pd.DataFrame]:
    return {name: pd.DataFrame(rows.get(name, []), columns=list(columns)) for name, columns in SHEET_COLUMNS.items()}


def admission_document(record: AdmissionIn, ids: AdmissionIds, patient_row: dict[str, Any], run_id: str) -> dict:
    rows: dict[str, list[dict[str, Any]]] = {
        "Paciente": [patient_row],
        "Ingresos": [record.ingreso_row(ids.admission_id, ids.admission_number, ids.triage_id)],
    }
    if record.triage and ids.triage_id is not None:
        rows["Triage"] = [record.triage.to_his_row(ids.triage_id, record.patient_id)]
    if record.attention_date:
        rows["Atencion"] = [{"OidIngreso": ids.admission_id, "FechaAtencion": record.attention_date}]
    return transform(_sheets(rows), run_id=run_id).admissions[0]


def line_document(kind: str, record: ServiceLineIn | MedicationLineIn, line_id: int, admission_date: datetime) -> dict:
    sheet = "Servicios" if kind == "service" else "MedicamentoInsumo"
    rows = {"Ingresos": [{"OidIngreso": record.admission_id, "FechaIngreso": admission_date}],
            sheet: [record.to_his_row(line_id)]}
    return transform(_sheets(rows), run_id="projection").admissions[0][LINE_ARRAYS[kind]][0]


def _is_current(admission_date: datetime, discharge: datetime, reference: datetime) -> bool:
    return admission_date <= reference and discharge >= reference - ACTIVE_WINDOW


async def _metadata(db) -> dict:
    metadata = await db[COLLECTIONS["metadata"]].find_one({"_id": "dataset"})
    if not metadata:
        raise DatasetNotLoadedError("Load the dataset before inserting records")
    return metadata


async def _touch_metadata(db, metadata: dict, counts: dict[str, int], reference: Optional[datetime] = None) -> None:
    """Bumps the dataset revision (cache keys) and, if needed, advances the reference date."""
    update: dict[str, Any] = {"$inc": {"revision": 1, **{f"counts.{k}": v for k, v in counts.items() if v}}}
    if reference is not None and reference > metadata["reference_date"]:
        update["$max"] = {"reference_date": reference}
        # "Now" moved forward: patients without activity in the last 24 h are no longer in a bed.
        await db[COLLECTIONS["admissions"]].update_many(
            {"currently_admitted": True, "estimated_discharge_date": {"$lt": reference - ACTIVE_WINDOW}},
            {"$set": {"currently_admitted": False}})
    await db[COLLECTIONS["metadata"]].update_one({"_id": "dataset"}, update)


async def project_admission(db, record: AdmissionIn, ids: AdmissionIds, patient_row: dict[str, Any],
                            origin: dict[str, Any]) -> bool:
    """Returns True when the document was created (False: it already existed)."""
    metadata = await _metadata(db)
    doc = await asyncio.to_thread(admission_document, record, ids, patient_row, str(metadata.get("etl_run_id")))
    reference = max(metadata["reference_date"], record.admission_date)
    doc["currently_admitted"] = _is_current(doc["admission_date"], doc["estimated_discharge_date"], reference)
    doc["record_origin"] = origin
    doc.pop("_id")
    result = await db[COLLECTIONS["admissions"]].update_one(
        {"_id": ids.admission_id}, {"$setOnInsert": doc}, upsert=True)
    created = result.upserted_id is not None
    await _touch_metadata(db, metadata, {"admissions": int(created)}, record.admission_date)
    return created


def _derived(admission: dict, kind: str, line: dict, lines: list[dict], reference: datetime) -> dict[str, Any]:
    """Fields the ETL derives from the lines, recomputed with the new line included."""
    dates = [d for d in (admission.get("estimated_discharge_date"), admission.get("hospitalization_date"),
                         admission["admission_date"], line.get("service_date")) if d is not None]
    discharge = max(dates)
    derived: dict[str, Any] = {
        "estimated_discharge_date": discharge,
        "length_of_stay_days": round((discharge - admission["admission_date"]).total_seconds() / 86400, 2),
        "currently_admitted": _is_current(admission["admission_date"], discharge, reference),
    }
    if kind == "service":
        specialties = Counter(item.get("specialty") for item in (*lines, line) if item.get("specialty"))
        if specialties:
            derived["primary_specialty"] = specialties.most_common(1)[0][0]
        surgeries = [dict(s) for s in admission.get("scheduled_surgeries") or []]
        for surgery in surgeries:  # a scheduled surgery counts as performed once its CUPS is provided
            if not surgery.get("performed") and surgery.get("cups_code") == line.get("cups_code"):
                surgery["performed"] = True
        derived["scheduled_surgeries"] = surgeries
        derived["surgeries_performed"] = sum(1 for s in surgeries if s.get("performed"))
    return derived


def _day(value: datetime) -> tuple[datetime, datetime]:
    start = value.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


async def refresh_medication_usage(db, when: datetime, code: str) -> None:
    start, end = _day(when)
    window = {"$gte": start, "$lt": end}
    rows = await db[COLLECTIONS["admissions"]].aggregate([
        {"$match": {"medications": {"$elemMatch": {"code": code, "service_date": window}}}},
        {"$unwind": "$medications"},
        {"$match": {"medications.code": code, "medications.service_date": window}},
        {"$group": {"_id": None, "quantity": {"$sum": "$medications.quantity"}, "lines": {"$sum": 1},
                    "name": {"$first": "$medications.name"}}},
    ]).to_list(length=1)
    if rows:
        values = {k: rows[0][k] for k in ("quantity", "lines", "name")}
        await db[COLLECTIONS["medication_usage_daily"]].update_one(
            {"date": start, "code": code}, {"$set": values}, upsert=True)


async def refresh_service_demand(db, when: datetime, specialty: str, area: str) -> None:
    start, end = _day(when)
    window = {"$gte": start, "$lt": end}
    rows = await db[COLLECTIONS["admissions"]].aggregate([
        {"$match": {"services": {"$elemMatch": {"specialty": specialty, "area": area, "service_date": window}}}},
        {"$unwind": "$services"},
        {"$match": {"services.specialty": specialty, "services.area": area, "services.service_date": window}},
        {"$group": {"_id": None, "lines": {"$sum": 1}, "quantity": {"$sum": "$services.quantity"},
                    "admissions": {"$addToSet": "$_id"}}},
    ]).to_list(length=1)
    if rows:
        values = {"lines": rows[0]["lines"], "quantity": rows[0]["quantity"], "admissions": len(rows[0]["admissions"])}
        await db[COLLECTIONS["service_demand_daily"]].update_one(
            {"date": start, "specialty": specialty, "area": area}, {"$set": values}, upsert=True)


async def project_line(db, kind: str, record: ServiceLineIn | MedicationLineIn, line_id: int) -> bool:
    """Returns True when the line was added (False: it was already there)."""
    array = LINE_ARRAYS[kind]
    admissions = db[COLLECTIONS["admissions"]]
    metadata = await _metadata(db)
    added = False
    for _ in range(MAX_LINE_ATTEMPTS):
        admission = await admissions.find_one(
            {"_id": record.admission_id},
            {"admission_date": 1, "hospitalization_date": 1, "estimated_discharge_date": 1, "record_revision": 1,
             "scheduled_surgeries": 1, array: 1})
        if admission is None:
            raise AdmissionNotProjectedError(f"Admission {record.admission_id} is not in MongoDB yet")
        lines = admission.get(array) or []
        if any(item.get("line_id") == line_id for item in lines):
            break  # already applied by a previous attempt
        line = await asyncio.to_thread(line_document, kind, record, line_id, admission["admission_date"])
        derived = _derived(admission, kind, line, lines, metadata["reference_date"])
        result = await admissions.update_one(
            {"_id": record.admission_id, "record_revision": admission.get("record_revision"),
             f"{array}.line_id": {"$ne": line_id}},
            {"$push": {array: line}, "$set": derived, "$inc": {"record_revision": 1}})
        if result.modified_count:
            added = True
            break
    else:
        raise ConcurrentUpdateError(f"Admission {record.admission_id} changed too often; retry later")

    # Recomputed from the source documents: correct even after a partial failure.
    if kind == "service":
        await refresh_service_demand(db, record.service_date, record.specialty, record.area)
    else:
        await refresh_medication_usage(db, record.service_date, record.code)
    await _touch_metadata(db, metadata, {array: int(added)})
    return added
