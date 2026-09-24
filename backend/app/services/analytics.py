"""Pure pandas analytics shared by the dashboard KPIs, the alert rules and the reports.

No I/O here: every function receives DataFrames, so it is fast to unit-test.
Column names of the lean admissions frame (see `frames_from_documents`):

admission_id, admission_date, admission_class, admission_route, wait_minutes, shift,
estimated_discharge_date, currently_admitted, primary_specialty, surgeries_scheduled,
surgeries_performed, bed_group, triage_level, sex, age_group, regime, insurer,
diagnosis_chapter, length_of_stay_days
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional

import numpy as np
import pandas as pd

ER_ROUTE = "Urgencias"

LEAN_ADMISSION_PROJECTION = {
    "_id": 1, "admission_date": 1, "admission_class": 1, "admission_route": 1,
    "wait_minutes": 1, "shift": 1, "estimated_discharge_date": 1, "currently_admitted": 1,
    "primary_specialty": 1, "surgeries_scheduled": 1, "surgeries_performed": 1,
    "bed.group": 1, "triage.level": 1, "patient.sex": 1, "patient.age_group": 1,
    "patient.regime": 1, "patient.insurer": 1, "diagnosis.chapter": 1, "length_of_stay_days": 1,
}
_RENAME = {
    "_id": "admission_id", "bed.group": "bed_group", "triage.level": "triage_level",
    "patient.sex": "sex", "patient.age_group": "age_group", "patient.regime": "regime",
    "patient.insurer": "insurer", "diagnosis.chapter": "diagnosis_chapter",
}


@dataclass
class Frames:
    admissions: pd.DataFrame
    capacity: pd.DataFrame           # bed_group, beds
    inventory: pd.DataFrame          # code, name, stock, avg_daily_consumption, days_of_inventory, ...
    medication_usage: pd.DataFrame   # date, code, name, quantity, lines
    service_demand: pd.DataFrame     # date, specialty, area, lines, quantity, admissions
    reference_date: pd.Timestamp
    data_start: pd.Timestamp
    run_id: str


def frames_from_documents(
    admissions: list[dict],
    capacity: list[dict],
    inventory: list[dict],
    medication_usage: list[dict],
    service_demand: list[dict],
    metadata: dict,
) -> Frames:
    lean = [{k: _get_path(doc, k) for k in LEAN_ADMISSION_PROJECTION} for doc in admissions]
    adm = pd.DataFrame(lean, columns=list(LEAN_ADMISSION_PROJECTION)).rename(columns=_RENAME)
    for col in ("admission_date", "estimated_discharge_date"):
        adm[col] = pd.to_datetime(adm[col])
    adm["triage_level"] = pd.to_numeric(adm["triage_level"], errors="coerce").astype("Int64")
    adm["wait_minutes"] = pd.to_numeric(adm["wait_minutes"], errors="coerce")
    adm["currently_admitted"] = adm["currently_admitted"].fillna(False).astype(bool)

    cap = pd.DataFrame(capacity or [], columns=["_id", "group", "beds", "virtual_beds"])
    cap = cap.rename(columns={"group": "bed_group"})[["bed_group", "beds"]]

    inv = pd.DataFrame(inventory or [])
    usage = pd.DataFrame(medication_usage or [], columns=["date", "code", "name", "quantity", "lines"])
    usage["date"] = pd.to_datetime(usage["date"])
    demand = pd.DataFrame(service_demand or [], columns=["date", "specialty", "area", "lines", "quantity", "admissions"])
    demand["date"] = pd.to_datetime(demand["date"])

    return Frames(
        admissions=adm,
        capacity=cap,
        inventory=inv,
        medication_usage=usage,
        service_demand=demand,
        reference_date=pd.Timestamp(metadata.get("reference_date")),
        data_start=pd.Timestamp(metadata.get("data_start")),
        run_id=str(metadata.get("etl_run_id", "")),
    )


def _get_path(doc: dict, path: str) -> Any:
    node: Any = doc
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


# --------------------------------------------------------------------------- #
# Filters and helpers
# --------------------------------------------------------------------------- #
def resolve_period(frames: Frames, start: Optional[date], end: Optional[date]) -> tuple[pd.Timestamp, pd.Timestamp]:
    start_ts = pd.Timestamp(start) if start else frames.data_start.normalize()
    end_ts = (pd.Timestamp(end) + pd.Timedelta(days=1) - pd.Timedelta(seconds=1)) if end else frames.reference_date
    return start_ts, min(end_ts, frames.reference_date + pd.Timedelta(days=1))


def filter_admissions(
    adm: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    bed_group: Optional[str] = None,
    specialty: Optional[str] = None,
) -> pd.DataFrame:
    mask = (adm["admission_date"] >= start) & (adm["admission_date"] <= end)
    if bed_group:
        mask &= adm["bed_group"] == bed_group
    if specialty:
        mask &= adm["primary_specialty"] == specialty
    return adm[mask]


def _records(df: pd.DataFrame) -> list[dict]:
    out = df.replace({np.nan: None}).to_dict("records")
    for row in out:
        for k, v in row.items():
            if isinstance(v, pd.Timestamp):
                row[k] = v.isoformat()
            elif isinstance(v, (np.integer,)):
                row[k] = int(v)
            elif isinstance(v, (np.floating,)):
                row[k] = None if np.isnan(v) else float(v)
            elif v is pd.NA:
                row[k] = None
    return out


def _round(value: Any, digits: int = 1) -> Optional[float]:
    if value is None or (isinstance(value, float) and np.isnan(value)) or value is pd.NA:
        return None
    return round(float(value), digits)


# --------------------------------------------------------------------------- #
# Occupancy (census)
# --------------------------------------------------------------------------- #
def current_occupancy(frames: Frames, bed_group: Optional[str] = None) -> list[dict]:
    adm = frames.admissions
    active = adm[adm["currently_admitted"]]
    counts = active.groupby("bed_group").size().rename("occupied")
    table = frames.capacity.set_index("bed_group").join(counts, how="left").fillna({"occupied": 0})
    table["occupied"] = table["occupied"].astype(int)
    table["occupancy_pct"] = (table["occupied"] / table["beds"].replace(0, np.nan) * 100).round(1)
    table = table.reset_index().sort_values("occupancy_pct", ascending=False)
    if bed_group:
        table = table[table["bed_group"] == bed_group]
    return _records(table[["bed_group", "occupied", "beds", "occupancy_pct"]])


def daily_census(adm: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """Patients in a bed at 12:00 of each day, by bed group (vectorised interval overlap)."""
    days = pd.date_range(start.normalize(), end.normalize(), freq="D")
    if len(days) == 0 or adm.empty:
        return pd.DataFrame(columns=["date", "bed_group", "census"])
    noon = (days + pd.Timedelta(hours=12)).to_numpy()
    rows = []
    for group, sub in adm.dropna(subset=["bed_group"]).groupby("bed_group"):
        a = sub["admission_date"].to_numpy()[:, None]
        d = sub["estimated_discharge_date"].to_numpy()[:, None]
        census = ((a <= noon) & (d >= noon)).sum(axis=0)
        rows.append(pd.DataFrame({"date": days, "bed_group": group, "census": census}))
    return pd.concat(rows, ignore_index=True)


def occupancy_trend(frames: Frames, start: pd.Timestamp, end: pd.Timestamp, bed_group: Optional[str] = None) -> dict:
    adm = frames.admissions
    # Admissions that overlap the period (not only those admitted inside it).
    overlap = adm[(adm["admission_date"] <= end) & (adm["estimated_discharge_date"] >= start)]
    if bed_group:
        overlap = overlap[overlap["bed_group"] == bed_group]
    census = daily_census(overlap, start, end)
    if census.empty:
        return {"daily": [], "by_group": []}
    beds = frames.capacity.set_index("bed_group")["beds"]
    total = census.groupby("date")["census"].sum().reset_index()
    total_beds = beds.loc[beds.index.intersection(census["bed_group"].unique())].sum()
    total["occupancy_pct"] = (total["census"] / total_beds * 100).round(1) if total_beds else None
    census["occupancy_pct"] = (census["census"] / census["bed_group"].map(beds) * 100).round(1)
    return {"daily": _records(total), "by_group": _records(census)}


# --------------------------------------------------------------------------- #
# Waiting times
# --------------------------------------------------------------------------- #
def er_waits(adm: pd.DataFrame) -> pd.Series:
    er = adm[(adm["admission_route"] == ER_ROUTE) & adm["wait_minutes"].notna()]
    return er["wait_minutes"]


def wait_summary(adm: pd.DataFrame) -> dict:
    waits = er_waits(adm)
    return {
        "mean_minutes": _round(waits.mean()),
        "median_minutes": _round(waits.median()),
        "p90_minutes": _round(waits.quantile(0.9)) if len(waits) else None,
        "patients": int(len(waits)),
    }


def wait_by_triage(adm: pd.DataFrame) -> list[dict]:
    er = adm[(adm["admission_route"] == ER_ROUTE) & adm["wait_minutes"].notna() & adm["triage_level"].notna()]
    table = (
        er.groupby("triage_level")["wait_minutes"]
        .agg(mean_minutes="mean", median_minutes="median", patients="size")
        .reset_index()
        .sort_values("triage_level")
    )
    table["mean_minutes"] = table["mean_minutes"].round(1)
    table["median_minutes"] = table["median_minutes"].round(1)
    table["triage_level"] = table["triage_level"].astype(int)
    return _records(table)


def wait_by_shift(adm: pd.DataFrame) -> list[dict]:
    er = adm[(adm["admission_route"] == ER_ROUTE) & adm["wait_minutes"].notna()]
    table = er.groupby("shift")["wait_minutes"].agg(mean_minutes="mean", patients="size").reset_index()
    table["mean_minutes"] = table["mean_minutes"].round(1)
    return _records(table)


# --------------------------------------------------------------------------- #
# Surgery, demand, medications
# --------------------------------------------------------------------------- #
def surgery_summary(adm: pd.DataFrame) -> dict:
    scheduled = int(adm["surgeries_scheduled"].fillna(0).sum())
    performed = int(adm["surgeries_performed"].fillna(0).sum())
    monthly = (
        adm.assign(month=adm["admission_date"].dt.to_period("M").astype(str))
        .groupby("month")[["surgeries_scheduled", "surgeries_performed"]]
        .sum()
        .reset_index()
    )
    return {
        "scheduled": scheduled,
        "performed": performed,
        "completion_pct": _round(performed / scheduled * 100) if scheduled else None,
        "monthly": _records(monthly),
    }


def admissions_by_group(adm: pd.DataFrame) -> list[dict]:
    table = adm.groupby("bed_group").size().rename("admissions").reset_index().sort_values("admissions", ascending=False)
    return _records(table)


def admissions_by_route(adm: pd.DataFrame) -> list[dict]:
    table = adm.groupby("admission_route").size().rename("admissions").reset_index().sort_values("admissions", ascending=False)
    return _records(table)


def _period(df: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    return df[(df["date"] >= start.normalize()) & (df["date"] <= end)]


def demand(frames: Frames, start: pd.Timestamp, end: pd.Timestamp, specialty: Optional[str], by: str, top: int = 15) -> list[dict]:
    df = _period(frames.service_demand, start, end)
    if specialty:
        df = df[df["specialty"] == specialty]
    table = (
        df.groupby(by)[["lines", "admissions"]].sum().reset_index()
        .sort_values("lines", ascending=False).head(top)
        .rename(columns={"lines": "services"})
    )
    return _records(table)


def medication_rotation(frames: Frames, start: pd.Timestamp, end: pd.Timestamp, top: int = 10, lowest: bool = False) -> list[dict]:
    df = _period(frames.medication_usage, start, end)
    table = df.groupby("name", dropna=True)["quantity"].sum().reset_index()
    table = table[table["quantity"] > 0].sort_values("quantity", ascending=lowest).head(top)
    return _records(table.rename(columns={"name": "medication"}))


def low_stock(frames: Frames, threshold_days: float) -> list[dict]:
    inv = frames.inventory
    if inv.empty:
        return []
    table = inv[inv["days_of_inventory"] < threshold_days].sort_values("days_of_inventory")
    cols = ["code", "name", "stock", "avg_daily_consumption", "days_of_inventory", "reorder_point"]
    return _records(table[cols])


# --------------------------------------------------------------------------- #
# De-identified patient table
# --------------------------------------------------------------------------- #
PATIENT_TABLE_COLUMNS = [
    "admission_id", "admission_date", "admission_route", "bed_group", "triage_level",
    "sex", "age_group", "diagnosis_chapter", "primary_specialty", "length_of_stay_days",
]


def patient_table(adm: pd.DataFrame, page: int, size: int) -> dict:
    table = adm.sort_values("admission_date", ascending=False)[PATIENT_TABLE_COLUMNS]
    total = len(table)
    page = max(page, 1)
    rows = table.iloc[(page - 1) * size : page * size]
    return {"total": total, "page": page, "size": size, "rows": _records(rows)}


def to_iso(value: Any) -> Optional[str]:
    if value is None or value is pd.NaT:
        return None
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return pd.Timestamp(value).isoformat()
    return str(value)
