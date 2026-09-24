"""In-process caches with single-flight loading.

Every cached value that depends on the dataset is keyed by the ETL `run_id`, so a new data
load can never serve stale numbers; `clear_all()` (called by `invalidate_cache`) also frees
the memory right after an ETL.

- `TTLCache`   bounded LRU with per-entry expiry (plain dict + insertion order, O(1)).
- `memoize`    async get-or-compute: concurrent requests for the same key wait for ONE
               computation instead of all running it (avoids thundering herds on KPIs,
               reports and alerts after a cache miss).
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Hashable

_MISSING = object()


class TTLCache:
    __slots__ = ("maxsize", "ttl", "_data", "_locks")

    def __init__(self, maxsize: int = 128, ttl: float = 3600.0) -> None:
        self.maxsize = maxsize
        self.ttl = ttl
        self._data: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()
        self._locks: dict[Hashable, asyncio.Lock] = {}

    def get(self, key: Hashable) -> Any:
        """Returns the cached value or the `_MISSING` sentinel."""
        entry = self._data.get(key)
        if entry is None:
            return _MISSING
        expires_at, value = entry
        if expires_at < time.monotonic():
            self._data.pop(key, None)
            return _MISSING
        self._data.move_to_end(key)
        return value

    def set(self, key: Hashable, value: Any) -> None:
        self._data[key] = (time.monotonic() + self.ttl, value)
        self._data.move_to_end(key)
        while len(self._data) > self.maxsize:
            self._data.popitem(last=False)

    def lock_for(self, key: Hashable) -> asyncio.Lock:
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        return lock

    def clear(self) -> None:
        self._data.clear()
        self._locks.clear()

    def __len__(self) -> int:
        return len(self._data)


_REGISTRY: list[TTLCache] = []


def register(maxsize: int = 128, ttl: float = 3600.0) -> TTLCache:
    """Creates a cache that is emptied by `clear_all()`."""
    cache = TTLCache(maxsize, ttl)
    _REGISTRY.append(cache)
    return cache


def clear_all() -> None:
    for cache in _REGISTRY:
        cache.clear()


def is_missing(value: Any) -> bool:
    return value is _MISSING


async def memoize(cache: TTLCache, key: Hashable, factory: Callable[[], Awaitable[Any]]) -> Any:
    """Get-or-compute with single flight per key."""
    value = cache.get(key)
    if value is not _MISSING:
        return value
    async with cache.lock_for(key):
        value = cache.get(key)
        if value is _MISSING:
            value = await factory()
            cache.set(key, value)
    cache._locks.pop(key, None)
    return value
