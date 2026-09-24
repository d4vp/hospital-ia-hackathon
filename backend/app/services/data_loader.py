"""ETL: DateBaseHIS.xlsx (7 sheets) -> MongoDB.

Collections produced (all field names in English, snake_case):

admissions              One embedded document per admission (OidIngreso):
                        patient, triage (+ derived `level` 1-5), bed, diagnosis (+ `chapter`),
                        services[], medications[], scheduled_surgeries[] (+ `performed`),
                        and derived fields: wait_minutes, shift, estimated_discharge_date,
                        length_of_stay_days, currently_admitted, primary_specialty.
bed_capacity            Distinct beds observed per bed group (capacity proxy).
inventory               SYNTHETIC stock per medication (the source has no inventory table).
                        Consumption is REAL; only `stock` and `expiration_date` are simulated.
medication_usage_daily  Real dispensed quantity per medication per day.
service_demand_daily    Real service lines per specialty/area per day.
metadata                Reference date, real categorical values (used in the agent prompt),
                        data-quality counters and the ETL run id.

Design notes
- Fully vectorised with pandas (no iterrows). The slow part is reading the .xlsx; the
  `calamine` engine (Rust) is used when installed, otherwise openpyxl.
- Mojibake (double-encoded UTF-8 such as 'POPAYÃN') is repaired in every text column.
- Idempotent: documents are replaced by _id and tagged with `etl_run_id`; documents
  from previous runs that no longer exist in the source are deleted.
- "Discharge" is not in the source. It is estimated as the last recorded service or
  medication for the admission. A patient is `currently_admitted` if the admission
  started before the reference date and had activity in the 24 h before it.
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
from pymongo import ASCENDING, DESCENDING, IndexModel, ReplaceOne

from app.core.config import COLLECTIONS, settings
from app.services.text_utils import (
    age_group,
    fix_mojibake_series,
    icd10_chapter,
    triage_level,
)

logger = logging.getLogger("etl")

SHEETS = (
    "Paciente",
    "Ingresos",
    "Triage",
    "Atencion",
    "Servicios",
    "MedicamentoInsumo",
    "ProgramacionCirugia",
)

ACTIVE_WINDOW = timedelta(hours=24)
INVENTORY_WINDOW_DAYS = 30
SYNTHETIC_SEED = 20260921
BATCH_SIZE = 1000


@dataclass
class DatasetBundle:
    run_id: str
    admissions: list[dict]
    bed_capacity: list[dict]
    inventory: list[dict]
    medication_usage_daily: list[dict]
    service_demand_daily: list[dict]
    metadata: dict
    quality: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def read_workbook(path: Path) -> dict[str, pd.DataFrame]:
    try:
        import python_calamine  # noqa: F401

        engine = "calamine"
    except ImportError:
        engine = "openpyxl"
    frames = pd.read_excel(path, sheet_name=None, engine=engine)
    missing = [s for s in SHEETS if s not in frames]
    if missing:
        raise ValueError(f"Missing sheets in workbook: {missing}. Found: {list(frames)}")
    return {name: frames[name] for name in SHEETS}


# --------------------------------------------------------------------------- #
# Generic helpers
# --------------------------------------------------------------------------- #
def _clean_text_columns(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Fixes mojibake and trims whitespace in every text column."""
    df = df.copy()
    fixed = 0
    for col in df.columns:
        if df[col].dtype == object or pd.api.types.is_string_dtype(df[col]):
            original = df[col]
            repaired = fix_mojibake_series(original).map(
                lambda v: v.strip() if isinstance(v, str) else v
            )
            fixed += int((original.astype("object") != repaired.astype("object")).sum())
            df[col] = repaired
    return df, fixed


def _to_datetime(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce")


def _native(value: Any) -> Any:
    """pandas / numpy scalar -> BSON-encodable Python value (NaN/NaT -> None)."""
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return None if pd.isna(value) else value.to_pydatetime()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and np.isnan(value):
        return None
    if value is pd.NaT or value is pd.NA:
        return None
    return value


def df_to_records(df: pd.DataFrame) -> list[dict]:
    columns = list(df.columns)
    arrays = [df[c].astype("object").to_numpy() for c in columns]
    return [
        {col: _native(arr[i]) for col, arr in zip(columns, arrays)}
        for i in range(len(df))
    ]


def _nest(flat: dict) -> dict:
    """{'bed.code': 'X'} -> {'bed': {'code': 'X'}}."""
    out: dict = {}
    for key, value in flat.items():
        parts = key.split(".")
        node = out
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return out


def _group_records(df: pd.DataFrame, key: str) -> dict[Any, list[dict]]:
    """Groups rows by `key` into lists of records (vectorised split, no iterrows)."""
    if df.empty:
        return {}
    df = df.sort_values(key, kind="stable")
    keys = df[key].to_numpy()
    records = df_to_records(df.drop(columns=[key]))
    uniques, starts = np.unique(keys, return_index=True)
    ends = list(starts[1:]) + [len(records)]
    return {_native(k): records[s:e] for k, s, e in zip(uniques, starts, ends)}


# --------------------------------------------------------------------------- #
# Transform
# --------------------------------------------------------------------------- #
def transform(frames: dict[str, pd.DataFrame], run_id: Optional[str] = None) -> DatasetBundle:
    run_id = run_id or uuid.uuid4().hex
    quality: dict[str, Any] = {"rows": {k: int(len(v)) for k, v in frames.items()}}
    mojibake_fixed = 0
    cleaned: dict[str, pd.DataFrame] = {}
    for name, df in frames.items():
        cleaned[name], fixed = _clean_text_columns(df)
        mojibake_fixed += fixed
    quality["mojibake_values_fixed"] = mojibake_fixed

    # ---- Admissions (base table) ----
    adm = cleaned["Ingresos"].rename(
        columns={
            "OidIngreso": "_id",
            "ConsecutivoIngreso": "admission_number",
            "IdPaciente": "patient_ref",
            "ClaseIngreso": "admission_class",
            "ViaIngreso": "admission_route",
            "TipoRiesgo": "risk_type",
            "FechaIngreso": "admission_date",
            "FechaHospitalizacion": "hospitalization_date",
            "OidTriageA": "triage_ref",
            "CodigoCama": "bed.code",
            "NombreCama": "bed.name",
            "NombreGrupoCama": "bed.group",
            "NombreSubgrupoCama": "bed.subgroup",
            "CodigoDiagnostico": "diagnosis.code",
            "NombreDiagnostico": "diagnosis.name",
        }
    )
    adm["admission_date"] = _to_datetime(adm["admission_date"])
    adm["hospitalization_date"] = _to_datetime(adm["hospitalization_date"])
    adm = adm.dropna(subset=["_id", "admission_date"]).drop_duplicates("_id")
    adm["_id"] = adm["_id"].astype("int64")
    adm["diagnosis.chapter"] = adm["diagnosis.code"].map(icd10_chapter)

    # ---- Attention (first medical attention) ----
    att = cleaned["Atencion"].rename(columns={"OidIngreso": "_id", "FechaAtencion": "attention_date"})
    att["attention_date"] = _to_datetime(att["attention_date"])
    att = att.dropna(subset=["_id"]).drop_duplicates("_id")
    att["_id"] = att["_id"].astype("int64")
    adm = adm.merge(att[["_id", "attention_date"]], on="_id", how="left")
    wait = (adm["attention_date"] - adm["admission_date"]).dt.total_seconds() / 60.0
    adm["wait_minutes"] = wait.where(wait >= 0).round(1)
    hour = adm["admission_date"].dt.hour
    adm["shift"] = np.where((hour >= 7) & (hour < 19), "day", "night")

    # ---- Patients ----
    pat = cleaned["Paciente"].rename(
        columns={
            "IdPaciente": "patient_ref",
            "TipoDocumento": "patient.document_type",
            "NombrePaciente": "patient.name",
            "FechaNacimiento": "patient.birth_date",
            "Sexo": "patient.sex",
            "Asegurador": "patient.insurer",
            "Regimen": "patient.regime",
            "Departamento": "patient.department",
            "Municipio": "patient.municipality",
            "Zona": "patient.zone",
        }
    ).drop_duplicates("patient_ref")
    pat["patient.birth_date"] = _to_datetime(pat["patient.birth_date"])
    pat["patient.patient_id"] = pat["patient_ref"]
    adm = adm.merge(pat, on="patient_ref", how="left")
    age = (adm["admission_date"] - adm["patient.birth_date"]).dt.days / 365.25
    adm["patient.age"] = age.where(age >= 0).round(1)
    adm["patient.age_group"] = adm["patient.age"].map(age_group)

    # ---- Triage ----
    tri = cleaned["Triage"].rename(
        columns={
            "OidTriage": "triage_ref",
            "FechaTriage": "triage.triage_date",
            "MotivoConsulta": "triage.chief_complaint",
            "TensionArterial": "triage.blood_pressure",
            "FrecuenciaCardiaca": "triage.heart_rate",
            "FrecuenciaRespiratoria": "triage.respiratory_rate",
            "Temperatura": "triage.temperature",
            "CodigoTriage": "triage.code",
            "ClasificacionTriage": "triage.classification",
        }
    )
    tri = tri.dropna(subset=["triage_ref"]).drop_duplicates("triage_ref")
    tri["triage.triage_date"] = _to_datetime(tri["triage.triage_date"])
    tri["triage.level"] = tri["triage.classification"].map(triage_level).astype("Int64")
    tri["triage.triage_id"] = tri["triage_ref"].astype("int64")
    tri["triage.code"] = pd.to_numeric(tri["triage.code"], errors="coerce").astype("Int64")
    tri = tri.drop(columns=[c for c in ("IdPaciente2",) if c in tri.columns])
    adm = adm.merge(tri, on="triage_ref", how="left")
    for col in ("triage.triage_id", "triage.code", "triage.level"):
        adm[col] = adm[col].astype("Int64")
    quality["admissions_without_triage"] = int(adm["triage.triage_id"].isna().sum())

    # ---- Services ----
    srv = cleaned["Servicios"].rename(
        columns={
            "OidIngreso": "admission_id",
            "OidS": "line_id",
            "CodigoServicio": "cups_code",
            "NombreServicio": "name",
            "Cantidad": "quantity",
            "FechaPrestacion": "service_date",
            "CodigoAreaServicio": "area_code",
            "AreaServicio": "area",
            "Especialidad": "specialty",
        }
    )
    srv["service_date"] = _to_datetime(srv["service_date"])
    srv["cups_code"] = srv["cups_code"].astype("string").str.strip()
    srv = srv.dropna(subset=["admission_id"])
    srv["admission_id"] = srv["admission_id"].astype("int64")

    # ---- Medications / supplies ----
    med = cleaned["MedicamentoInsumo"].rename(
        columns={
            "OidIngreso": "admission_id",
            "OidMI": "line_id",
            "CodigoServicio": "code",
            "NombreServicio": "name",
            "Cantidad": "quantity",
            "FechaPrestacion": "service_date",
            "AreaServicio": "area",
            "Especialidad": "specialty",
        }
    )
    med["service_date"] = _to_datetime(med["service_date"])
    med["code"] = med["code"].astype("string").str.strip()
    med = med.dropna(subset=["admission_id"])
    med["admission_id"] = med["admission_id"].astype("int64")

    # ---- Estimated discharge (last activity) & census flag ----
    last_activity = pd.concat(
        [srv[["admission_id", "service_date"]], med[["admission_id", "service_date"]]]
    ).groupby("admission_id")["service_date"].max()
    adm["last_activity"] = adm["_id"].map(last_activity)
    adm["estimated_discharge_date"] = adm[
        ["admission_date", "hospitalization_date", "last_activity"]
    ].max(axis=1)
    adm["length_of_stay_days"] = (
        (adm["estimated_discharge_date"] - adm["admission_date"]).dt.total_seconds() / 86400
    ).round(2)
    reference_date = adm["admission_date"].max()
    adm["currently_admitted"] = (adm["admission_date"] <= reference_date) & (
        adm["estimated_discharge_date"] >= reference_date - ACTIVE_WINDOW
    )

    # ---- Primary specialty (most frequent specialty in the admission's services) ----
    spec = (
        srv.dropna(subset=["specialty"])
        .groupby(["admission_id", "specialty"])
        .size()
        .reset_index(name="n")
        .sort_values(["admission_id", "n"], ascending=[True, False])
        .drop_duplicates("admission_id")
        .set_index("admission_id")["specialty"]
    )
    adm["primary_specialty"] = adm["_id"].map(spec)

    # ---- Scheduled surgeries: performed = the scheduled CUPS appears in Servicios ----
    sur = cleaned["ProgramacionCirugia"].rename(
        columns={
            "ConsecutivoProgramacion": "schedule_number",
            "IdPaciente": "patient_ref",
            "OidIngreso": "admission_id",
            "CodigoServicio": "cups_code",
        }
    )
    quality["surgery_rows_total"] = int(len(sur))
    quality["surgery_rows_without_admission"] = int(sur["admission_id"].isna().sum())
    sur = sur.dropna(subset=["admission_id"])
    sur["admission_id"] = sur["admission_id"].astype("int64")
    outside = ~sur["admission_id"].isin(adm["_id"])
    quality["surgery_rows_outside_period"] = int(outside.sum())
    sur = sur[~outside].copy()
    sur["cups_code"] = sur["cups_code"].astype("string").str.strip()
    performed_keys = set(zip(srv["admission_id"].tolist(), srv["cups_code"].astype(object).tolist()))
    sur["performed"] = [
        (a, c) in performed_keys
        for a, c in zip(sur["admission_id"].tolist(), sur["cups_code"].astype(object).tolist())
    ]
    sur["schedule_number"] = sur["schedule_number"].astype("string")
    sur = sur.drop(columns=["patient_ref"]).drop_duplicates(["admission_id", "schedule_number", "cups_code"])
    surgery_counts = sur.groupby("admission_id").agg(
        surgeries_scheduled=("cups_code", "size"), surgeries_performed=("performed", "sum")
    )
    adm = adm.join(surgery_counts, on="_id")
    adm["surgeries_scheduled"] = adm["surgeries_scheduled"].fillna(0).astype("int64")
    adm["surgeries_performed"] = adm["surgeries_performed"].fillna(0).astype("int64")

    # ---- Build embedded documents ----
    services_by_adm = _group_records(srv, "admission_id")
    meds_by_adm = _group_records(med, "admission_id")
    surgeries_by_adm = _group_records(sur, "admission_id")

    adm = adm.drop(columns=["patient_ref", "triage_ref", "last_activity"])
    admissions: list[dict] = []
    for flat in df_to_records(adm):
        doc = _nest(flat)
        if doc.get("triage", {}).get("triage_id") is None:
            doc["triage"] = None
        doc["services"] = services_by_adm.get(doc["_id"], [])
        doc["medications"] = meds_by_adm.get(doc["_id"], [])
        doc["scheduled_surgeries"] = surgeries_by_adm.get(doc["_id"], [])
        doc["etl_run_id"] = run_id
        admissions.append(doc)

    # ---- Derived collections ----
    bed_capacity = _build_bed_capacity(adm)
    usage_daily = _build_medication_usage_daily(med)
    demand_daily = _build_service_demand_daily(srv)
    inventory = build_synthetic_inventory(med, reference_date)

    metadata = {
        "_id": "dataset",
        "etl_run_id": run_id,
        "loaded_at": datetime.now(timezone.utc),
        "reference_date": _native(reference_date),
        "data_start": _native(adm["admission_date"].min()),
        "counts": {
            "admissions": len(admissions),
            "services": int(len(srv)),
            "medications": int(len(med)),
            "scheduled_surgeries": int(len(sur)),
            "inventory_items": len(inventory),
        },
        "categorical_values": _categorical_values(adm, srv, med),
        "quality": quality,
    }
    return DatasetBundle(
        run_id=run_id,
        admissions=admissions,
        bed_capacity=bed_capacity,
        inventory=inventory,
        medication_usage_daily=usage_daily,
        service_demand_daily=demand_daily,
        metadata=metadata,
        quality=quality,
    )


def _build_bed_capacity(adm: pd.DataFrame) -> list[dict]:
    beds = adm.dropna(subset=["bed.group", "bed.code"])
    grouped = beds.groupby("bed.group").agg(
        beds=("bed.code", "nunique"),
        virtual_beds=("bed.name", lambda s: int(s[s.str.contains("VIRTUAL", na=False)].nunique())),
    )
    overrides = settings.bed_capacity_overrides
    return [
        {
            "_id": str(group),
            "group": str(group),
            "beds": int(overrides.get(str(group), row.beds)),
            "virtual_beds": int(row.virtual_beds),
            "source": "configured" if str(group) in overrides else "distinct_beds_observed",
        }
        for group, row in grouped.iterrows()  # at most ~10 rows
    ]


def _build_medication_usage_daily(med: pd.DataFrame) -> list[dict]:
    usage = (
        med.assign(date=med["service_date"].dt.normalize())
        .dropna(subset=["date"])
        .groupby(["date", "code", "name"], dropna=False)
        .agg(quantity=("quantity", "sum"), lines=("quantity", "size"))
        .reset_index()
    )
    return df_to_records(usage)


def _build_service_demand_daily(srv: pd.DataFrame) -> list[dict]:
    demand = (
        srv.assign(date=srv["service_date"].dt.normalize())
        .dropna(subset=["date"])
        .groupby(["date", "specialty", "area"], dropna=False)
        .agg(lines=("quantity", "size"), quantity=("quantity", "sum"), admissions=("admission_id", "nunique"))
        .reset_index()
    )
    return df_to_records(demand)


def build_synthetic_inventory(med: pd.DataFrame, reference_date: pd.Timestamp) -> list[dict]:
    """SYNTHETIC inventory. Real average daily consumption, simulated stock level.

    avg_daily_consumption = quantity dispensed in the last 30 days / 30
    (falls back to the whole period when there was no use in the last 30 days).
    stock = avg_daily_consumption * U(0.5, 45) days of cover (seeded => reproducible).
    """
    if med.empty:
        return []
    window_start = reference_date - pd.Timedelta(days=INVENTORY_WINDOW_DAYS)
    total_days = max((med["service_date"].max() - med["service_date"].min()).days, 1)
    recent = med[med["service_date"] >= window_start].groupby("code")["quantity"].sum() / INVENTORY_WINDOW_DAYS
    overall = med.groupby("code")["quantity"].sum() / total_days
    names = med.dropna(subset=["name"]).drop_duplicates("code").set_index("code")["name"]
    avg_daily = recent.reindex(overall.index).fillna(overall).where(lambda s: s > 0, overall)
    avg_daily = avg_daily[avg_daily > 0].sort_index()

    rng = np.random.default_rng(SYNTHETIC_SEED)
    cover_days = rng.uniform(0.5, 45.0, size=len(avg_daily))
    expiry_days = rng.integers(15, 720, size=len(avg_daily))
    ref = pd.Timestamp(reference_date).normalize()

    items = []
    for (code, daily), cover, expiry in zip(avg_daily.items(), cover_days, expiry_days):
        stock = max(1, int(np.ceil(daily * cover)))
        items.append(
            {
                "_id": str(code),
                "code": str(code),
                "name": names.get(code),
                "stock": stock,
                "avg_daily_consumption": round(float(daily), 3),
                "days_of_inventory": round(stock / float(daily), 2),
                "reorder_point": int(np.ceil(daily * 7)),
                "expiration_date": (ref + pd.Timedelta(days=int(expiry))).to_pydatetime(),
                "synthetic": True,
            }
        )
    return items


def _categorical_values(adm: pd.DataFrame, srv: pd.DataFrame, med: pd.DataFrame) -> dict:
    def distinct(series: pd.Series, top: Optional[int] = None) -> list:
        counts = series.dropna().value_counts()
        values = counts.index.tolist()[:top] if top else counts.index.tolist()
        return [_native(v) for v in values]

    return {
        "admission_class": distinct(adm["admission_class"]),
        "admission_route": distinct(adm["admission_route"]),
        "risk_type": distinct(adm["risk_type"]),
        "bed.group": distinct(adm["bed.group"]),
        "bed.subgroup": distinct(adm["bed.subgroup"]),
        "triage.level": sorted(int(v) for v in adm["triage.level"].dropna().unique()),
        "patient.sex": distinct(adm["patient.sex"]),
        "patient.regime": distinct(adm["patient.regime"]),
        "patient.zone": distinct(adm["patient.zone"]),
        "patient.age_group": distinct(adm["patient.age_group"]),
        "diagnosis.chapter": distinct(adm["diagnosis.chapter"]),
        "shift": ["day", "night"],
        "services.specialty": distinct(srv["specialty"], top=40),
        "services.area": distinct(srv["area"], top=40),
        "medications.area": distinct(med["area"], top=20),
    }


# --------------------------------------------------------------------------- #
# Load
# --------------------------------------------------------------------------- #
def _replace_collection(db, name: str, docs: list[dict]) -> None:
    collection = db[name]
    collection.delete_many({})
    for i in range(0, len(docs), 5000):
        collection.insert_many(docs[i : i + 5000], ordered=False)


def write_bundle(db, bundle: DatasetBundle) -> dict:
    admissions = db[COLLECTIONS["admissions"]]
    upserted = modified = 0
    for i in range(0, len(bundle.admissions), BATCH_SIZE):
        ops = [ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in bundle.admissions[i : i + BATCH_SIZE]]
        result = admissions.bulk_write(ops, ordered=False)
        upserted += result.upserted_count
        modified += result.modified_count
    removed = admissions.delete_many({"etl_run_id": {"$ne": bundle.run_id}}).deleted_count

    _replace_collection(db, COLLECTIONS["bed_capacity"], bundle.bed_capacity)
    _replace_collection(db, COLLECTIONS["inventory"], bundle.inventory)
    _replace_collection(db, COLLECTIONS["medication_usage_daily"], bundle.medication_usage_daily)
    _replace_collection(db, COLLECTIONS["service_demand_daily"], bundle.service_demand_daily)
    db[COLLECTIONS["metadata"]].replace_one({"_id": "dataset"}, bundle.metadata, upsert=True)

    admissions.create_indexes(
        [
            IndexModel([("admission_date", DESCENDING)]),
            IndexModel([("bed.group", ASCENDING), ("currently_admitted", ASCENDING)]),
            IndexModel([("admission_route", ASCENDING), ("admission_date", DESCENDING)]),
            IndexModel([("triage.level", ASCENDING)]),
            IndexModel([("primary_specialty", ASCENDING)]),
            IndexModel([("etl_run_id", ASCENDING)]),
        ]
    )
    db[COLLECTIONS["inventory"]].create_index([("days_of_inventory", ASCENDING)])
    db[COLLECTIONS["medication_usage_daily"]].create_index([("date", ASCENDING), ("code", ASCENDING)])
    db[COLLECTIONS["service_demand_daily"]].create_index([("date", ASCENDING)])
    return {"inserted": upserted, "updated": modified, "removed_stale": removed}


def load_excel_to_mongo(db=None, path: Optional[Path] = None) -> dict:
    """ETL entry point. The path is always the server-side configured file."""
    from app.db.mongo import get_sync_db

    path = Path(path or settings.excel_path)
    if not path.exists():
        raise FileNotFoundError(f"Source workbook not found on the server: {path.name}")
    db = db if db is not None else get_sync_db()

    t0 = time.perf_counter()
    frames = read_workbook(path)
    t_read = time.perf_counter()
    bundle = transform(frames)
    t_transform = time.perf_counter()
    write_stats = write_bundle(db, bundle)
    t_write = time.perf_counter()

    summary = {
        "run_id": bundle.run_id,
        "admissions_processed": len(bundle.admissions),
        **write_stats,
        "inventory_items_synthetic": len(bundle.inventory),
        "reference_date": bundle.metadata["reference_date"],
        "quality": bundle.quality,
        "seconds": {
            "read": round(t_read - t0, 1),
            "transform": round(t_transform - t_read, 1),
            "write": round(t_write - t_transform, 1),
        },
    }
    logger.info("etl_completed", extra={"data": summary})
    return summary


if __name__ == "__main__":  # python -m app.services.data_loader
    from app.core.logging_config import configure_logging

    configure_logging()
    print(load_excel_to_mongo())
