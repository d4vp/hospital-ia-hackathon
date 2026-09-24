"""Optional billing module: administrative analysis of billable activity.

Enabled with BILLING_ENABLED (default true) and exposed only to administrators. The HIS
workbook has no prices, so the module reports BILLABLE VOLUME (service and medication lines
and units) by insurer (EPS), health regime, month and item. When BILLING_TARIFFS is
configured, units are valued with those unit prices (per CUPS / medication code, plus a
"default"), and every amount is labelled as an estimate.

Performance: the two line-level aggregations run once per ETL run (read-only handle,
concurrently) and are cached as a compact DataFrame; each filter combination is then a
pandas groupby, memoized and executed off the event loop.
"""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Optional

import numpy as np
import pandas as pd

from app.core.config import COLLECTIONS, settings
from app.db.mongo import get_readonly_db
from app.services import cache
from app.services.analytics import Frames, _records

KINDS = {"services": "cups_code", "medications": "code"}  # embedded array -> item code field
TOP_ITEMS = 15
TOP_GROUPS = 20
UNKNOWN = "SIN DATO"

_lines_cache = cache.register(maxsize=2, ttl=settings.CACHE_TTL_SECONDS)
_summary_cache = cache.register(maxsize=128, ttl=settings.CACHE_TTL_SECONDS)


def _pipeline(array: str, code_field: str) -> list[dict]:
    item = f"${array}"
    return [
        {"$project": {"_id": 0, "patient.insurer": 1, "patient.regime": 1, array: 1}},
        {"$unwind": item},
        {"$match": {f"{array}.service_date": {"$ne": None}}},
        {"$group": {
            "_id": {"insurer": "$patient.insurer", "regime": "$patient.regime",
                    "year": {"$year": f"{item}.service_date"}, "month": {"$month": f"{item}.service_date"},
                    "code": f"{item}.{code_field}"},
            "name": {"$first": f"{item}.name"},
            "units": {"$sum": f"{item}.quantity"},
            "lines": {"$sum": 1},
        }},
    ]


async def load_lines(db, run_id: str) -> pd.DataFrame:
    """Billable lines grouped by insurer, regime, month and item (one row per group)."""
    async def load() -> pd.DataFrame:
        admissions = get_readonly_db(db)[COLLECTIONS["admissions"]]
        results = await asyncio.gather(*(
            admissions.aggregate(_pipeline(array, code), allowDiskUse=True).to_list(length=None)
            for array, code in KINDS.items()
        ))
        return await asyncio.to_thread(_lines_frame, dict(zip(KINDS, results)))

    return await cache.memoize(_lines_cache, run_id, load)


def _lines_frame(groups: dict[str, list[dict]]) -> pd.DataFrame:
    rows = [
        {"kind": kind, "insurer": g["_id"].get("insurer") or UNKNOWN, "regime": g["_id"].get("regime") or UNKNOWN,
         "month": f"{int(g['_id']['year']):04d}-{int(g['_id']['month']):02d}",
         "code": str(g["_id"].get("code") or ""), "name": g.get("name") or "",
         "units": float(g.get("units") or 0), "lines": int(g.get("lines") or 0)}
        for kind, docs in groups.items() for g in docs if g["_id"].get("year")
    ]
    columns = ["kind", "insurer", "regime", "month", "code", "name", "units", "lines"]
    return _price(pd.DataFrame(rows, columns=columns))


def _price(df: pd.DataFrame) -> pd.DataFrame:
    """Adds unit `price` and `amount` (NaN when no tariff applies)."""
    tariffs = settings.billing_tariffs
    df = df.copy()
    df["price"] = np.nan
    for kind in KINDS:
        table = tariffs.get(kind) or {}
        if not table:
            continue
        mask = df["kind"] == kind
        prices = df.loc[mask, "code"].map(table)
        if "default" in table:
            prices = prices.fillna(table["default"])
        df.loc[mask, "price"] = prices
    df["amount"] = df["units"] * df["price"]
    return df


def _group(df: pd.DataFrame, by: list[str], priced: bool, top: int) -> list[dict]:
    if df.empty:
        return []
    table = df.pivot_table(index=by, columns="kind", values=["units", "lines"], aggfunc="sum", fill_value=0)
    table.columns = [f"{kind}_{metric}" for metric, kind in table.columns]
    table = table.reindex(columns=[f"{k}_{m}" for k in KINDS for m in ("lines", "units")], fill_value=0)
    table["amount"] = df.groupby(by)["amount"].sum(min_count=1) if priced else np.nan
    table = table.reset_index().sort_values("amount" if priced else "services_lines", ascending=False).head(top)
    return _records(table)


def summarize(frames: Frames, lines: pd.DataFrame, start: Optional[date], end: Optional[date],
              insurer: Optional[str]) -> dict:
    priced = bool(lines["price"].notna().any()) if not lines.empty else False
    df = lines
    if start:
        df = df[df["month"] >= f"{start:%Y-%m}"]
    if end:
        df = df[df["month"] <= f"{end:%Y-%m}"]
    if insurer:
        df = df[df["insurer"] == insurer]

    adm = frames.admissions
    mask = pd.Series(True, index=adm.index)
    if start:
        mask &= adm["admission_date"] >= pd.Timestamp(start)
    if end:
        mask &= adm["admission_date"] < pd.Timestamp(end) + pd.Timedelta(days=1)
    if insurer:
        mask &= adm["insurer"] == insurer
    admissions = adm[mask]

    per_kind = df.groupby("kind")[["lines", "units"]].sum()
    total_amount = float(df["amount"].sum(min_count=1)) if priced and df["amount"].notna().any() else None

    monthly = (df.groupby(["month", "kind"])[["lines", "units", "amount"]].sum(min_count=1).reset_index()
               .sort_values("month")) if not df.empty else pd.DataFrame()
    items = (df.groupby(["kind", "code", "name"])[["units", "lines", "amount"]].sum(min_count=1).reset_index()
             .sort_values("amount" if priced else "units", ascending=False).head(TOP_ITEMS)) if not df.empty else pd.DataFrame()
    by_insurer_adm = admissions.assign(insurer=admissions["insurer"].fillna(UNKNOWN)).groupby("insurer").size()
    by_insurer = _group(df, ["insurer"], priced, TOP_GROUPS)
    for row in by_insurer:
        row["admissions"] = int(by_insurer_adm.get(row["insurer"], 0))

    return {
        "currency": settings.BILLING_CURRENCY,
        "priced": priced,
        "period": {"start": start.isoformat() if start else None, "end": end.isoformat() if end else None},
        "insurers": sorted(lines["insurer"].dropna().unique().tolist()) if not lines.empty else [],
        "totals": {
            "admissions": int(len(admissions)),
            "service_lines": int(per_kind["lines"].get("services", 0)),
            "service_units": float(per_kind["units"].get("services", 0)),
            "medication_lines": int(per_kind["lines"].get("medications", 0)),
            "medication_units": float(per_kind["units"].get("medications", 0)),
            "estimated_amount": round(total_amount, 2) if total_amount is not None else None,
        },
        "by_insurer": by_insurer,
        "by_regime": _group(df, ["regime"], priced, TOP_GROUPS),
        "monthly": _records(monthly) if not monthly.empty else [],
        "top_items": _records(items) if not items.empty else [],
    }


async def billing_summary(db, frames: Frames, start: Optional[date], end: Optional[date],
                          insurer: Optional[str]) -> dict[str, Any]:
    lines = await load_lines(db, frames.run_id)
    key = (frames.run_id, start, end, insurer)
    return await cache.memoize(_summary_cache, key,
                               lambda: asyncio.to_thread(summarize, frames, lines, start, end, insurer))
