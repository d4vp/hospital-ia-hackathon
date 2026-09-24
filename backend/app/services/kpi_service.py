"""Dashboard payload built from the cached analytical frames (see analytics.py).

The `cached_*` coroutines are what the routes call: results are memoized per ETL run and
filter combination, and the pandas work runs in a worker thread so a heavy dashboard never
blocks the event loop (chat, alerts and health keep answering meanwhile).
"""
from __future__ import annotations

import asyncio
from datetime import date
from typing import Optional

from app.core.config import settings
from app.services import analytics as an
from app.services import cache
from app.services.analytics import Frames

_kpi_cache = cache.register(maxsize=256, ttl=settings.CACHE_TTL_SECONDS)


async def cached_filter_options(frames: Frames) -> dict:
    return await cache.memoize(_kpi_cache, ("filters", frames.run_id),
                               lambda: asyncio.to_thread(filter_options, frames))


async def cached_dashboard(frames: Frames, start: Optional[date], end: Optional[date],
                           bed_group: Optional[str], specialty: Optional[str]) -> dict:
    key = ("dashboard", frames.run_id, start, end, bed_group, specialty)
    return await cache.memoize(_kpi_cache, key,
                               lambda: asyncio.to_thread(dashboard, frames, start, end, bed_group, specialty))


async def cached_patients(frames: Frames, start: Optional[date], end: Optional[date], bed_group: Optional[str],
                          specialty: Optional[str], page: int, size: int) -> dict:
    key = ("patients", frames.run_id, start, end, bed_group, specialty, page, size)
    return await cache.memoize(_kpi_cache, key,
                               lambda: asyncio.to_thread(patients, frames, start, end, bed_group, specialty, page, size))


def filter_options(frames: Frames) -> dict:
    adm = frames.admissions
    return {
        "date_min": frames.data_start.date().isoformat(),
        "date_max": frames.reference_date.date().isoformat(),
        "bed_groups": sorted(adm["bed_group"].dropna().unique().tolist()),
        "specialties": adm["primary_specialty"].dropna().value_counts().index.tolist()[:60],
    }


def dashboard(frames: Frames, start: Optional[date], end: Optional[date],
              bed_group: Optional[str], specialty: Optional[str]) -> dict:
    start_ts, end_ts = an.resolve_period(frames, start, end)
    adm = an.filter_admissions(frames.admissions, start_ts, end_ts, bed_group, specialty)
    occupancy_now = an.current_occupancy(frames, bed_group)
    total_occupied = sum(r["occupied"] for r in occupancy_now)
    total_beds = sum(r["beds"] for r in occupancy_now)
    low = an.low_stock(frames, settings.ALERT_INVENTORY_DAYS_THRESHOLD)
    waits = an.wait_summary(adm)
    surgery = an.surgery_summary(adm)

    return {
        "period": {"start": start_ts.isoformat(), "end": end_ts.isoformat(),
                   "reference_date": frames.reference_date.isoformat()},
        "cards": {
            "admissions": int(len(adm)),
            "occupied_beds": total_occupied,
            "total_beds": total_beds,
            "occupancy_pct": round(total_occupied / total_beds * 100, 1) if total_beds else None,
            "er_wait_mean_minutes": waits["mean_minutes"],
            "er_wait_median_minutes": waits["median_minutes"],
            "low_stock_items": len(low),
            "surgery_completion_pct": surgery["completion_pct"],
        },
        "occupancy_now": occupancy_now,
        "occupancy_trend": an.occupancy_trend(frames, start_ts, end_ts, bed_group),
        "wait": {**waits, "by_triage": an.wait_by_triage(adm), "by_shift": an.wait_by_shift(adm)},
        "surgeries": surgery,
        "admissions_by_group": an.admissions_by_group(adm),
        "admissions_by_route": an.admissions_by_route(adm),
        "demand_by_specialty": an.demand(frames, start_ts, end_ts, specialty, "specialty"),
        "demand_by_area": an.demand(frames, start_ts, end_ts, specialty, "area"),
        "top_medications": an.medication_rotation(frames, start_ts, end_ts, 10, lowest=False),
        "lowest_rotation_medications": an.medication_rotation(frames, start_ts, end_ts, 10, lowest=True),
        "low_stock": low[:25],
        "low_stock_threshold_days": settings.ALERT_INVENTORY_DAYS_THRESHOLD,
        "inventory_is_synthetic": True,
    }


def patients(frames: Frames, start: Optional[date], end: Optional[date], bed_group: Optional[str],
             specialty: Optional[str], page: int, size: int) -> dict:
    start_ts, end_ts = an.resolve_period(frames, start, end)
    adm = an.filter_admissions(frames.admissions, start_ts, end_ts, bed_group, specialty)
    return an.patient_table(adm, page, size)
