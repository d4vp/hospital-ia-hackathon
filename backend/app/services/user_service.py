"""Users with roles `admin` and `user`, stored in the `users` collection.

- Passwords are hashed with bcrypt; hashes never leave this module.
- A first admin is created from BOOTSTRAP_ADMIN_* only when the collection is empty.
- Simple in-memory brute-force protection on login (5 failures / 15 minutes per email).
- Root-access protection: the LAST active administrator can never be deactivated nor
  demoted to "user" (by anyone, including another admin). The check and the write run
  under a lock, and the result is re-verified after writing (compensating rollback), so
  two admins demoting each other at the same time cannot leave the system without one.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError

from app.core.config import COLLECTIONS, settings
from app.core.security import hash_password, validate_password_policy, verify_password

logger = logging.getLogger("users")

ROLES = ("admin", "user")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MAX_FAILED_LOGINS = 5
LOCK_SECONDS = 15 * 60

_failed_logins: dict[str, list[float]] = defaultdict(list)


class UserError(ValueError):
    pass


class LoginLockedError(RuntimeError):
    pass


class LastAdminError(UserError):
    """The change would leave the system without an active administrator."""


LAST_ADMIN_MESSAGE = ("This is the only active administrator: it cannot be deactivated or changed to "
                      "the 'user' role. Promote another user to administrator first.")
_admin_guard = asyncio.Lock()


def public_user(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "email": doc["email"],
        "full_name": doc.get("full_name", ""),
        "role": doc["role"],
        "is_active": doc.get("is_active", True),
        "created_at": doc.get("created_at"),
        "last_login_at": doc.get("last_login_at"),
    }


def _normalize_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        raise UserError("Invalid email address")
    return email


async def ensure_indexes(db) -> None:
    await db[COLLECTIONS["users"]].create_index([("email", ASCENDING)], unique=True)


async def bootstrap_admin(db) -> Optional[str]:
    users = db[COLLECTIONS["users"]]
    if await users.count_documents({}, limit=1):
        return None
    if not settings.BOOTSTRAP_ADMIN_PASSWORD:
        logger.warning("no_users_and_no_bootstrap_password: set BOOTSTRAP_ADMIN_PASSWORD or run app.scripts.create_admin")
        return None
    user = await create_user(db, settings.BOOTSTRAP_ADMIN_EMAIL, settings.BOOTSTRAP_ADMIN_PASSWORD,
                             settings.BOOTSTRAP_ADMIN_NAME, "admin")
    logger.info("bootstrap_admin_created", extra={"data": {"email": user["email"]}})
    return user["id"]


async def create_user(db, email: str, password: str, full_name: str, role: str) -> dict:
    if role not in ROLES:
        raise UserError(f"Role must be one of {ROLES}")
    validate_password_policy(password)
    doc = {
        "email": _normalize_email(email),
        "full_name": (full_name or "").strip()[:120],
        "role": role,
        "password_hash": hash_password(password),
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
        "last_login_at": None,
    }
    # Explicit check + unique index: duplicates are refused even if the index could not be built.
    if await db[COLLECTIONS["users"]].count_documents({"email": doc["email"]}, limit=1):
        raise UserError("A user with that email already exists")
    try:
        result = await db[COLLECTIONS["users"]].insert_one(doc)
    except DuplicateKeyError as exc:
        raise UserError("A user with that email already exists") from exc
    doc["_id"] = result.inserted_id
    return public_user(doc)


async def list_users(db) -> list[dict]:
    """Users plus `is_last_admin`, so the UI can lock the controls that the API would refuse."""
    cursor = db[COLLECTIONS["users"]].find({}, {"password_hash": 0}).sort("created_at", ASCENDING)
    users = [d async for d in cursor]
    sole_admin = [d for d in users if _is_active_admin(d)]
    last_admin_id = sole_admin[0]["_id"] if len(sole_admin) == 1 else None
    return [{**public_user(d), "is_last_admin": d["_id"] == last_admin_id} for d in users]


def _object_id(user_id: str) -> ObjectId:
    try:
        return ObjectId(user_id)
    except (InvalidId, TypeError) as exc:
        raise UserError("Invalid user id") from exc


async def get_user(db, user_id: str) -> Optional[dict]:
    return await db[COLLECTIONS["users"]].find_one({"_id": _object_id(user_id)})


async def update_user(db, user_id: str, changes: dict[str, Any], acting_admin_id: str) -> dict:
    oid = _object_id(user_id)
    update: dict[str, Any] = {}
    if changes.get("full_name") is not None:
        update["full_name"] = str(changes["full_name"]).strip()[:120]
    if changes.get("role") is not None:
        if changes["role"] not in ROLES:
            raise UserError(f"Role must be one of {ROLES}")
        update["role"] = changes["role"]
    if changes.get("is_active") is not None:
        update["is_active"] = bool(changes["is_active"])
    if changes.get("password"):
        validate_password_policy(changes["password"])
        update["password_hash"] = hash_password(changes["password"])
    if not update:
        raise UserError("Nothing to update")
    removes_admin = update.get("is_active") is False or update.get("role") == "user"
    if removes_admin and str(oid) == acting_admin_id:
        raise UserError("You cannot deactivate or demote your own account")
    update["updated_at"] = datetime.now(timezone.utc)
    if not removes_admin:
        return public_user(await _apply(db, oid, update))
    async with _admin_guard:  # serialises every change that can remove an administrator
        target = await db[COLLECTIONS["users"]].find_one({"_id": oid})
        if not target:
            raise UserError("User not found")
        if _is_active_admin(target) and await count_active_admins(db, exclude=oid) == 0:
            raise LastAdminError(LAST_ADMIN_MESSAGE)
        result = await _apply(db, oid, update)
        if await count_active_admins(db) == 0:  # another process won a race: undo and refuse
            await db[COLLECTIONS["users"]].update_one(
                {"_id": oid}, {"$set": {"role": target["role"], "is_active": target.get("is_active", True)}})
            logger.warning("last_admin_rollback", extra={"data": {"user_id": str(oid)}})
            raise LastAdminError(LAST_ADMIN_MESSAGE)
    return public_user(result)


def _is_active_admin(doc: dict) -> bool:
    return doc.get("role") == "admin" and doc.get("is_active", True)


async def count_active_admins(db, exclude: Optional[ObjectId] = None) -> int:
    query: dict[str, Any] = {"role": "admin", "is_active": True}
    if exclude is not None:
        query["_id"] = {"$ne": exclude}
    return await db[COLLECTIONS["users"]].count_documents(query)


async def _apply(db, oid: ObjectId, update: dict[str, Any]) -> dict:
    result = await db[COLLECTIONS["users"]].find_one_and_update({"_id": oid}, {"$set": update}, return_document=True)
    if not result:
        raise UserError("User not found")
    return result


async def authenticate(db, email: str, password: str) -> Optional[dict]:
    try:
        key = _normalize_email(email)
    except UserError:
        return None
    now = time.monotonic()
    recent = [ts for ts in _failed_logins[key] if now - ts < LOCK_SECONDS]
    _failed_logins[key] = recent
    if len(recent) >= MAX_FAILED_LOGINS:
        raise LoginLockedError("Too many failed attempts. Try again in 15 minutes.")

    user = await db[COLLECTIONS["users"]].find_one({"email": key})
    if not user or not user.get("is_active", True) or not verify_password(password, user["password_hash"]):
        _failed_logins[key].append(now)
        return None
    _failed_logins.pop(key, None)
    await db[COLLECTIONS["users"]].update_one({"_id": user["_id"]}, {"$set": {"last_login_at": datetime.now(timezone.utc)}})
    return user
