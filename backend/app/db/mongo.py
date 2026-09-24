"""MongoDB clients.

- A synchronous PyMongo client for the ETL (batch job, runs in a worker thread).
- An asynchronous Motor client for the FastAPI routes (non-blocking).
Both are created lazily so importing modules (e.g. in tests) never opens sockets.
"""
from functools import lru_cache

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import MongoClient
from pymongo.database import Database

from app.core.config import settings


@lru_cache
def _sync_client() -> MongoClient:
    return MongoClient(settings.MONGO_URI, serverSelectionTimeoutMS=5000, tz_aware=False)


@lru_cache
def _async_client() -> AsyncIOMotorClient:
    return AsyncIOMotorClient(settings.MONGO_URI, serverSelectionTimeoutMS=5000, tz_aware=False)


def get_sync_db() -> Database:
    return _sync_client()[settings.DB_NAME]


_db_override: AsyncIOMotorDatabase | None = None


def set_async_db_override(db) -> None:
    """Used by the tests to inject an in-memory database (mongomock-motor)."""
    global _db_override
    _db_override = db


def get_async_db() -> AsyncIOMotorDatabase:
    if _db_override is not None:
        return _db_override
    return _async_client()[settings.DB_NAME]


async def ping() -> bool:
    try:
        await get_async_db().command("ping")
        return True
    except Exception:  # noqa: BLE001 - health check must never raise
        return False
