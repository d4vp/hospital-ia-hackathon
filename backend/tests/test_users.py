"""User management rules: the last active administrator is untouchable; auth and lockout."""
import pytest
from bson import ObjectId

from app.core.config import COLLECTIONS
from app.core.security import PasswordPolicyError
from app.services import user_service
from app.services.user_service import LastAdminError, UserError

PASSWORD = "Secure/12345"


async def _user(db, email, role="user"):
    return await user_service.create_user(db, email, PASSWORD, email.split("@")[0], role)


async def test_sole_admin_cannot_be_deactivated_or_demoted(db):
    admin = await _user(db, "root@h.local", "admin")
    for change in ({"is_active": False}, {"role": "user"}, {"is_active": False, "role": "user"}):
        with pytest.raises(LastAdminError):
            await user_service.update_user(db, admin["id"], change, acting_admin_id="another-admin")
    stored = await db[COLLECTIONS["users"]].find_one({"_id": ObjectId(admin["id"])})
    assert stored["role"] == "admin" and stored["is_active"] is True
    # Non-critical changes to the sole admin are still allowed.
    renamed = await user_service.update_user(db, admin["id"], {"full_name": "Root"}, "another-admin")
    assert renamed["full_name"] == "Root"


async def test_admin_can_be_removed_while_another_admin_remains(db):
    first = await _user(db, "a@h.local", "admin")
    second = await _user(db, "b@h.local", "admin")
    demoted = await user_service.update_user(db, first["id"], {"role": "user"}, acting_admin_id=second["id"])
    assert demoted["role"] == "user"
    with pytest.raises(LastAdminError):  # now `second` is the only one left
        await user_service.update_user(db, second["id"], {"is_active": False}, acting_admin_id=first["id"])


async def test_inactive_admins_do_not_count(db):
    active = await _user(db, "on@h.local", "admin")
    inactive = await _user(db, "off@h.local", "admin")
    await db[COLLECTIONS["users"]].update_one({"_id": ObjectId(inactive["id"])}, {"$set": {"is_active": False}})
    with pytest.raises(LastAdminError):
        await user_service.update_user(db, active["id"], {"role": "user"}, acting_admin_id="x")


async def test_list_users_flags_the_last_admin(db):
    admin = await _user(db, "solo@h.local", "admin")
    await _user(db, "u@h.local")
    flags = {u["email"]: u["is_last_admin"] for u in await user_service.list_users(db)}
    assert flags == {"solo@h.local": True, "u@h.local": False}
    await _user(db, "second@h.local", "admin")
    assert not any(u["is_last_admin"] for u in await user_service.list_users(db))
    assert admin["id"]


async def test_race_is_rolled_back(db, monkeypatch):
    """Another process removed the other admin between our check and our write."""
    target = await _user(db, "t@h.local", "admin")
    await _user(db, "o@h.local", "admin")
    counts = iter([1, 0])  # check: one other admin exists; re-verification: none left

    async def fake_count(_db, exclude=None):
        return next(counts)

    monkeypatch.setattr(user_service, "count_active_admins", fake_count)
    with pytest.raises(LastAdminError):
        await user_service.update_user(db, target["id"], {"is_active": False}, acting_admin_id="x")
    stored = await db[COLLECTIONS["users"]].find_one({"_id": ObjectId(target["id"])})
    assert stored["is_active"] is True and stored["role"] == "admin"


async def test_other_update_rules(db):
    admin = await _user(db, "me@h.local", "admin")
    user = await _user(db, "you@h.local")
    with pytest.raises(UserError, match="own account"):
        await user_service.update_user(db, admin["id"], {"role": "user"}, acting_admin_id=admin["id"])
    with pytest.raises(UserError, match="Nothing"):
        await user_service.update_user(db, user["id"], {}, admin["id"])
    with pytest.raises(UserError, match="Invalid user id"):
        await user_service.update_user(db, "nope", {"full_name": "x"}, admin["id"])
    with pytest.raises(UserError, match="not found"):
        await user_service.update_user(db, str(ObjectId()), {"full_name": "x"}, admin["id"])
    with pytest.raises(UserError, match="Role"):
        await user_service.update_user(db, user["id"], {"role": "root"}, admin["id"])
    with pytest.raises(PasswordPolicyError):
        await user_service.update_user(db, user["id"], {"password": "short"}, admin["id"])
    updated = await user_service.update_user(db, user["id"], {"password": "Another/12345", "is_active": False}, admin["id"])
    assert updated["is_active"] is False


async def test_create_user_validation(db):
    await _user(db, "dup@h.local")
    with pytest.raises(UserError, match="already exists"):
        await _user(db, "DUP@h.local")
    with pytest.raises(UserError, match="email"):
        await _user(db, "not-an-email")
    with pytest.raises(UserError, match="Role"):
        await user_service.create_user(db, "r@h.local", PASSWORD, "x", "root")


async def test_authentication_and_lockout(db):
    user_service._failed_logins.clear()
    await _user(db, "login@h.local")
    assert (await user_service.authenticate(db, "LOGIN@h.local", PASSWORD))["email"] == "login@h.local"
    assert await user_service.authenticate(db, "bad-email", PASSWORD) is None
    for _ in range(user_service.MAX_FAILED_LOGINS):
        assert await user_service.authenticate(db, "login@h.local", "wrong-password") is None
    with pytest.raises(user_service.LoginLockedError):
        await user_service.authenticate(db, "login@h.local", PASSWORD)
    user_service._failed_logins.clear()


async def test_bootstrap_admin(db, monkeypatch):
    monkeypatch.setattr(user_service.settings, "BOOTSTRAP_ADMIN_PASSWORD", "")
    assert await user_service.bootstrap_admin(db) is None
    monkeypatch.setattr(user_service.settings, "BOOTSTRAP_ADMIN_PASSWORD", PASSWORD)
    assert await user_service.bootstrap_admin(db)
    assert await user_service.bootstrap_admin(db) is None  # only when the collection is empty


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
async def test_users_api(admin_client, user_client):
    assert (await user_client.get("/users")).status_code == 403
    users = (await admin_client.get("/users")).json()
    staff = next(u for u in users if u["email"] == "staff@hospital.local")
    assert (await admin_client.patch(f"/users/{staff['id']}", json={"role": "admin"})).json()["role"] == "admin"
    own = await admin_client.patch(f"/users/{admin_client.user['id']}", json={"role": "user"})
    assert own.status_code == 400  # nobody can demote or deactivate their own account
    created = await admin_client.post("/users", json={"email": "x@h.local", "password": PASSWORD, "role": "user"})
    assert created.status_code == 201
    assert (await admin_client.post("/users", json={"email": "x@h.local", "password": PASSWORD})).status_code == 400
    assert (await admin_client.patch(f"/users/{staff['id']}", json={"role": "root"})).status_code == 422


async def test_last_admin_conflict_over_http(admin_client, user_client, monkeypatch):
    """Through the API the acting admin is itself active, so the rule shows up in a race: 409."""
    staff_id = user_client.user["id"]
    await admin_client.patch(f"/users/{staff_id}", json={"role": "admin"})

    async def nobody_else(_db, exclude=None):
        return 0

    monkeypatch.setattr(user_service, "count_active_admins", nobody_else)
    response = await admin_client.patch(f"/users/{staff_id}", json={"is_active": False})
    assert response.status_code == 409 and "only active administrator" in response.json()["detail"]


async def test_login_and_me(db, anon_client):
    user_service._failed_logins.clear()
    await _user(db, "api@h.local")
    bad = await anon_client.post("/auth/login", json={"email": "api@h.local", "password": "nope-nope-1"})
    assert bad.status_code == 401
    ok = await anon_client.post("/auth/login", json={"email": "api@h.local", "password": PASSWORD})
    token = ok.json()["access_token"]
    me = await anon_client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert me.json()["email"] == "api@h.local"
    assert (await anon_client.get("/auth/me", headers={"Authorization": "Bearer broken"})).status_code == 401
    assert (await anon_client.get("/auth/me")).status_code == 401
    for _ in range(user_service.MAX_FAILED_LOGINS):
        await anon_client.post("/auth/login", json={"email": "api@h.local", "password": "nope-nope-1"})
    assert (await anon_client.post("/auth/login", json={"email": "api@h.local", "password": PASSWORD})).status_code == 429
    user_service._failed_logins.clear()
