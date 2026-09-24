"""Reads the analytical frames from MongoDB once per ETL run and caches them in memory.

The dataset (~18k admissions) fits comfortably in memory; recomputing KPIs with pandas
from a cached frame is faster and simpler than re-running aggregations on every request.
The cache is invalidated automatically when a new ETL run id appears in `metadata`.

Metadata itself is read on almost every request, so it is kept for a few seconds
(METADATA_CACHE_TTL_SECONDS) instead of costing one round trip each time. The ETL of this
process clears it immediately through `invalidate_cache()`.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from app.core.config import COLLECTIONS, settings
from app.services import cache
from app.services.analytics import LEAN_ADMISSION_PROJECTION, Frames, frames_from_documents


class DatasetNotLoadedError(RuntimeError):
    """Raised when the ETL has not been executed yet."""


_cache: dict[str, Frames] = {}
_lock = asyncio.Lock()
_metadata_cache = cache.register(maxsize=4, ttl=settings.METADATA_CACHE_TTL_SECONDS)
_capacity_cache = cache.register(maxsize=4, ttl=settings.CACHE_TTL_SECONDS)


async def get_metadata(db) -> Optional[dict]:
    key = getattr(db, "name", "")
    metadata = _metadata_cache.get(key)
    if cache.is_missing(metadata):
        metadata = await db[COLLECTIONS["metadata"]].find_one({"_id": "dataset"})
        if metadata:  # never cache "not loaded": the first ETL must be visible at once
            _metadata_cache.set(key, metadata)
    return metadata


async def get_capacity_docs(db, run_id: str) -> list[dict]:
    """Bed capacity documents ({group, beds, ...}) for the agent prompts, once per ETL run."""
    async def load() -> list[dict]:
        return await db[COLLECTIONS["bed_capacity"]].find({}).to_list(length=None)

    return await cache.memoize(_capacity_cache, run_id, load)


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
        collections = (
            db[COLLECTIONS["admissions"]].find({}, LEAN_ADMISSION_PROJECTION),
            db[COLLECTIONS["bed_capacity"]].find({}),
            db[COLLECTIONS["inventory"]].find({}),
            db[COLLECTIONS["medication_usage_daily"]].find({}, {"_id": 0}),
            db[COLLECTIONS["service_demand_daily"]].find({}, {"_id": 0}),
        )
        # The five reads are independent: run them concurrently.
        admissions, capacity, inventory, usage, demand = await asyncio.gather(
            *(cursor.to_list(length=None) for cursor in collections)
        )
        # pandas work runs in a worker thread so other requests keep being served meanwhile.
        frames = await asyncio.to_thread(frames_from_documents, admissions, capacity, inventory, usage, demand, metadata)
        _cache.clear()
        _cache[run_id] = frames
        return frames


def invalidate_cache() -> None:
    """Drops the frames and every derived cache (KPIs, alerts, reports, chat answers)."""
    _cache.clear()
    cache.clear_all()
