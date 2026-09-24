"""Read-only database facade for everything the AI agent executes.

The agent (LLM or Plan B) never receives a real Motor database. It receives a
`ReadOnlyDatabase`, whose collections expose ONLY read methods. Write, admin and
command methods (insert_*, update_*, replace_*, delete_*, drop*, bulk_write,
find_one_and_*, create_index, rename, command, watch, ...) simply do not exist on it:
any access raises `ReadOnlyViolation` before a socket is touched.

`aggregate` additionally rejects the two stages that write ($out, $merge) at any depth.

Layers (defence in depth):
1. query_guard   strict whitelist of operations, stages and operators (LLM output).
2. this facade   no write method is reachable from the agent code path.
3. MongoDB role  optional MONGO_READONLY_URI with a user that only has the `read` role,
                 so even a bug in layers 1-2 is refused by the server.
"""
from __future__ import annotations

from typing import Any, Iterable

WRITE_STAGES = frozenset({"$out", "$merge"})


class ReadOnlyViolation(PermissionError):
    """An attempt to write or run an admin command through the agent connection."""


def _contains_write_stage(node: Any) -> bool:
    if isinstance(node, dict):
        return any(key in WRITE_STAGES or _contains_write_stage(value) for key, value in node.items())
    if isinstance(node, list):
        return any(_contains_write_stage(item) for item in node)
    return False


class ReadOnlyCollection:
    __slots__ = ("_collection", "name")

    def __init__(self, collection: Any) -> None:
        object.__setattr__(self, "_collection", collection)
        object.__setattr__(self, "name", collection.name)

    # ---- the only operations the agent can run ----
    def find(self, filter: dict | None = None, projection: dict | None = None, **kwargs: Any):
        return self._collection.find(filter or {}, projection, **kwargs)

    async def find_one(self, filter: dict | None = None, projection: dict | None = None, **kwargs: Any):
        return await self._collection.find_one(filter or {}, projection, **kwargs)

    def aggregate(self, pipeline: list[dict], **kwargs: Any):
        if _contains_write_stage(pipeline):
            raise ReadOnlyViolation("Write stages ($out/$merge) are not allowed on the read-only connection")
        return self._collection.aggregate(pipeline, **kwargs)

    async def count_documents(self, filter: dict, **kwargs: Any) -> int:
        return await self._collection.count_documents(filter, **kwargs)

    async def distinct(self, key: str, filter: dict | None = None, **kwargs: Any) -> list:
        return await self._collection.distinct(key, filter or {}, **kwargs)

    # ---- everything else is refused ----
    def __getattr__(self, attr: str) -> Any:
        raise ReadOnlyViolation(f"Operation '{attr}' is not allowed on the read-only connection")

    def __setattr__(self, attr: str, value: Any) -> None:
        raise ReadOnlyViolation("The read-only collection is immutable")


class ReadOnlyDatabase:
    __slots__ = ("_db", "_allowed", "name")

    def __init__(self, db: Any, allowed_collections: Iterable[str]) -> None:
        object.__setattr__(self, "_db", db)
        object.__setattr__(self, "_allowed", frozenset(allowed_collections))
        object.__setattr__(self, "name", getattr(db, "name", ""))

    def __getitem__(self, collection: str) -> ReadOnlyCollection:
        if collection not in self._allowed:
            raise ReadOnlyViolation(f"Collection not readable by the agent: {collection}")
        return ReadOnlyCollection(self._db[collection])

    def __getattr__(self, attr: str) -> Any:
        # db.command, db.drop_collection, db.create_collection, db.admissions (attribute access) ...
        raise ReadOnlyViolation(f"Operation '{attr}' is not allowed on the read-only connection")

    def __setattr__(self, attr: str, value: Any) -> None:
        raise ReadOnlyViolation("The read-only database is immutable")


def as_readonly(db: Any, allowed_collections: Iterable[str]) -> ReadOnlyDatabase:
    return db if isinstance(db, ReadOnlyDatabase) else ReadOnlyDatabase(db, allowed_collections)
