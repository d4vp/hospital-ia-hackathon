"""Users with roles `admin` and `user`, stored in the `users` collection.

- Passwords are hashed with bcrypt; hashes never leave this module.
- A first admin is created from BOOTSTRAP_ADMIN_* only when the collection is empty.
- Simple in-memory brute-force protection on login (5 failures / 15 minutes per email).
"""
from __future__ import annotations

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
    try:
        result = await db[COLLECTIONS["users"]].insert_one(doc)
    except DuplicateKeyError as exc:
        raise UserError("A user with that email already exists") from exc
    doc["_id"] = result.inserted_id
    return public_user(doc)


async def list_users(db) -> list[dict]:
    cursor = db[COLLECTIONS["users"]].find({}, {"password_hash": 0}).sort("created_at", ASCENDING)
    return [public_user(d) async for d in cursor]


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
    if str(oid) == acting_admin_id and (update.get("is_active") is False or update.get("role") == "user"):
        raise UserError("You cannot deactivate or demote your own account")
    if update.get("is_active") is False or update.get("role") == "user":
        active_admins = await db[COLLECTIONS["users"]].count_documents({"role": "admin", "is_active": True, "_id": {"$ne": oid}})
        target = await db[COLLECTIONS["users"]].find_one({"_id": oid})
        if target and target["role"] == "admin" and active_admins == 0:
            raise UserError("At least one active admin is required")
    if not update:
        raise UserError("Nothing to update")
    update["updated_at"] = datetime.now(timezone.utc)
    result = await db[COLLECTIONS["users"]].find_one_and_update({"_id": oid}, {"$set": update}, return_document=True)
    if not result:
        raise UserError("User not found")
    return public_user(result)


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
