"""Reads the analytical frames from MongoDB once per ETL run and caches them in memory.

The dataset (~18k admissions) fits comfortably in memory; recomputing KPIs with pandas
from a cached frame is faster and simpler than re-running aggregations on every request.
The cache is invalidated automatically when a new ETL run id appears in `metadata`.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from app.core.config import COLLECTIONS
from app.services.analytics import LEAN_ADMISSION_PROJECTION, Frames, frames_from_documents


class DatasetNotLoadedError(RuntimeError):
    """Raised when the ETL has not been executed yet."""


_cache: dict[str, Frames] = {}
_lock = asyncio.Lock()


async def get_metadata(db) -> Optional[dict]:
    return await db[COLLECTIONS["metadata"]].find_one({"_id": "dataset"})


async def get_frames(db) -> Frames:
    metadata = await get_metadata(db)
    if not metadata:
        raise DatasetNotLoadedError("No data loaded yet. An administrator must upload the workbook first.")
    run_id = str(metadata.get("etl_run_id"))
    if run_id in _cache:
        return _cache[run_id]
    async with _lock:
        if run_id in _cache:
            return _cache[run_id]
        admissions = await db[COLLECTIONS["admissions"]].find({}, LEAN_ADMISSION_PROJECTION).to_list(length=None)
        capacity = await db[COLLECTIONS["bed_capacity"]].find({}).to_list(length=None)
        inventory = await db[COLLECTIONS["inventory"]].find({}).to_list(length=None)
        usage = await db[COLLECTIONS["medication_usage_daily"]].find({}, {"_id": 0}).to_list(length=None)
        demand = await db[COLLECTIONS["service_demand_daily"]].find({}, {"_id": 0}).to_list(length=None)
        frames = frames_from_documents(admissions, capacity, inventory, usage, demand, metadata)
        _cache.clear()
        _cache[run_id] = frames
        return frames


def invalidate_cache() -> None:
    _cache.clear()
