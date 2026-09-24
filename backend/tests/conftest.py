"""Test database: in-memory mongomock-motor by default, or a real MongoDB when
TEST_MONGO_URI is set (recommended in CI: `TEST_MONGO_URI=mongodb://localhost:27017`)."""
import os
import uuid

import pytest

from app.core.config import COLLECTIONS
from app.core.shield import limiter
from app.db.mongo import set_async_db_override
from app.services.data_loader import transform
from app.services.data_repository import invalidate_cache
from tests.fixtures import INVENTORY_FIXTURE, build_frames


@pytest.fixture(autouse=True)
def _fast_bcrypt(monkeypatch):
    """bcrypt cost 4 in tests (same algorithm, ~100x faster than production cost 12)."""
    from app.core import security

    monkeypatch.setattr(security, "BCRYPT_ROUNDS", 4)


def _make_db():
    uri = os.getenv("TEST_MONGO_URI")
    if uri:
        from motor.motor_asyncio import AsyncIOMotorClient

        client = AsyncIOMotorClient(uri)
        return client, client[f"test_hospital_{uuid.uuid4().hex[:8]}"]
    from mongomock_motor import AsyncMongoMockClient

    client = AsyncMongoMockClient()
    return client, client["test_hospital"]


@pytest.fixture
def bundle():
    return transform(build_frames(), run_id="test-run")


@pytest.fixture
async def db(bundle):
    client, database = _make_db()
    await database[COLLECTIONS["admissions"]].insert_many(bundle.admissions)
    await database[COLLECTIONS["bed_capacity"]].insert_many(bundle.bed_capacity)
    await database[COLLECTIONS["inventory"]].insert_many([dict(d) for d in INVENTORY_FIXTURE])
    await database[COLLECTIONS["medication_usage_daily"]].insert_many(bundle.medication_usage_daily)
    await database[COLLECTIONS["service_demand_daily"]].insert_many(bundle.service_demand_daily)
    await database[COLLECTIONS["metadata"]].insert_one(bundle.metadata)
    set_async_db_override(database)
    invalidate_cache()
    limiter.reset()
    yield database
    set_async_db_override(None)
    invalidate_cache()
    if os.getenv("TEST_MONGO_URI"):
        await client.drop_database(database.name)


async def _client_for(db, email: str, role: str):
    import httpx

    from app.core.security import create_access_token
    from app.main import app
    from app.services import user_service

    user = await user_service.create_user(db, email, "Secure/12345", role.title(), role)
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test/api",
                             headers={"Authorization": f"Bearer {create_access_token(user['id'], role)}"}), user


@pytest.fixture
async def admin_client(db):
    http, user = await _client_for(db, "boss@hospital.local", "admin")
    http.user = user
    async with http:
        yield http


@pytest.fixture
async def user_client(db):
    http, user = await _client_for(db, "staff@hospital.local", "user")
    http.user = user
    async with http:
        yield http


@pytest.fixture
async def anon_client(db):
    import httpx

    from app.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test/api") as http:
        yield http
