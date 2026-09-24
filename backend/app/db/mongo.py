"""MongoDB clients.

- A synchronous PyMongo client for the ETL (batch job, runs in a worker thread).
- An asynchronous Motor client for the FastAPI routes (non-blocking).
- A read-only facade for the AI agent (`get_readonly_db`), optionally backed by a second
  client authenticated as a MongoDB user with only the `read` role (MONGO_READONLY_URI).
All clients are created lazily so importing modules (e.g. in tests) never opens sockets.
"""
from functools import lru_cache

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import MongoClient
from pymongo.database import Database

from app.core.config import AGENT_READABLE_COLLECTIONS, settings
from app.db.readonly import ReadOnlyDatabase, as_readonly


@lru_cache
def _sync_client() -> MongoClient:
    return MongoClient(settings.MONGO_URI, serverSelectionTimeoutMS=5000, tz_aware=False)


@lru_cache
def _async_client() -> AsyncIOMotorClient:
    return AsyncIOMotorClient(settings.MONGO_URI, serverSelectionTimeoutMS=5000, tz_aware=False)


@lru_cache
def _readonly_client() -> AsyncIOMotorClient:
    return AsyncIOMotorClient(settings.MONGO_READONLY_URI, serverSelectionTimeoutMS=5000, tz_aware=False,
                              readPreference="secondaryPreferred", retryWrites=False)


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


def get_readonly_db(db: AsyncIOMotorDatabase | ReadOnlyDatabase | None = None) -> ReadOnlyDatabase:
    """Database handle for agent-generated queries: only read methods, only whitelisted collections.

    With MONGO_READONLY_URI configured (and no test override) the handle uses the read-only
    MongoDB user, so the server itself rejects any write that could slip through.
    """
    if isinstance(db, ReadOnlyDatabase):
        return db
    if _db_override is None and settings.MONGO_READONLY_URI.strip():
        return as_readonly(_readonly_client()[settings.DB_NAME], AGENT_READABLE_COLLECTIONS)
    return as_readonly(db if db is not None else get_async_db(), AGENT_READABLE_COLLECTIONS)


async def ping() -> bool:
    try:
        await get_async_db().command("ping")
        return True
    except Exception:  # noqa: BLE001 - health check must never raise
        return False
