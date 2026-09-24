"""Security layer between the LLM and MongoDB.

Every query produced by the agent (LLM or Plan B) goes through:

1. `validate_query`   structure, collection whitelist, recursive operator deny-list
                      (at ANY depth: inside $expr, $group, $facet, $elemMatch ...),
                      forbidden (PII) field references, and field-existence checks.
2. `enforce_privacy`  mandatory backend projection / $unset of PII fields, whatever the
                      LLM asked for.
3. `execute_query`    read-only execution with maxTimeMS and a hard row limit.
4. `sanitize_results` defence in depth: removes PII keys from the returned rows before
                      they are sent to OpenAI or to the frontend.
"""
from __future__ import annotations

import copy
from typing import Any, Iterable, Optional

from app.core.config import PII_PATHS, settings
from app.services.schema_catalog import COLLECTION_FIELDS, is_forbidden_path, is_known_path


class QueryValidationError(ValueError):
    """The generated query is unsafe or refers to fields that do not exist."""


MAX_LIMIT = 200
DEFAULT_LIMIT = 50
MAX_STAGES = 25

FORBIDDEN_OPERATORS = {
    "$out", "$merge", "$function", "$accumulator", "$where", "$lookup", "$graphLookup",
    "$unionWith", "$collStats", "$currentOp", "$indexStats", "$listSessions",
    "$listLocalSessions", "$planCacheStats", "$documents", "$changeStream",
    "$listSearchIndexes", "$search", "$searchMeta", "$vectorSearch", "$shardedDataDistribution",
}
# References that would copy a whole sub-document containing PII under another name.
FORBIDDEN_REFERENCES = {"$patient", "$triage", "$diagnosis", "$$ROOT", "$$CURRENT"}
SHAPE_CHANGING_STAGES = {
    "$group", "$project", "$replaceRoot", "$replaceWith", "$count", "$bucket",
    "$bucketAuto", "$facet", "$sortByCount",
}
LOGICAL_OPERATORS = {"$and", "$or", "$nor"}

# Keys removed anywhere in a result row (defence in depth).
_PII_LEAF_KEYS = {"birth_date", "patient_id", "chief_complaint"}
# Keys removed only inside these parents.
_PII_NESTED = {"patient": {"name", "birth_date", "patient_id"}, "diagnosis": {"code", "name"},
               "triage": {"chief_complaint"}}


# --------------------------------------------------------------------------- #
# Recursive scanning helpers
# --------------------------------------------------------------------------- #
def _walk(node: Any) -> Iterable[tuple[Optional[str], Any]]:
    """Yields (key, value) for every dict entry at every depth, and list items with key None."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key, value
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield None, item
            yield from _walk(item)


def _check_operators(node: Any) -> None:
    for key, value in _walk(node):
        if isinstance(key, str) and key in FORBIDDEN_OPERATORS:
            raise QueryValidationError(f"Operator not allowed: {key}")
        if isinstance(value, str) and (value in FORBIDDEN_REFERENCES or value.startswith("$$ROOT")
                                       or value.startswith("$$CURRENT")):
            raise QueryValidationError(f"Reference not allowed: {value}")


def _field_refs(node: Any) -> Iterable[str]:
    """'$bed.group' -> 'bed.group' (skips $$variables and operators)."""
    if isinstance(node, str):
        if node.startswith("$") and not node.startswith("$$") and len(node) > 1:
            yield node[1:]
    elif isinstance(node, dict):
        for value in node.values():
            yield from _field_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _field_refs(item)


def _filter_paths(node: Any, prefix: str = "") -> Iterable[str]:
    """Field paths used as keys in a query filter ($match / find), incl. $elemMatch."""
    if isinstance(node, list):
        for item in node:
            yield from _filter_paths(item, prefix)
        return
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        if key in LOGICAL_OPERATORS:
            yield from _filter_paths(value, prefix)
        elif key == "$expr":
            yield from (f"{prefix}{r}" if prefix else r for r in _field_refs(value))
        elif key == "$elemMatch":
            yield from _filter_paths(value, prefix)
        elif key == "$not":
            yield from _filter_paths(value, prefix)
        elif key.startswith("$"):
            continue  # comparison operator ($gte, $in, ...): its value is a literal
        else:
            path = f"{prefix}{key}"
            yield path
            if isinstance(value, dict) and "$elemMatch" in value:
                yield from _filter_paths(value["$elemMatch"], path + ".")


RESHAPED = "__reshaped__"


def _check_paths(collection: str, paths: Iterable[str], known: Optional[set[str]]) -> None:
    """Rejects PII fields always, and unknown fields when the document shape is known.

    known = None                 -> shape unknown (after $replaceRoot/$facet): only PII check
    known contains RESHAPED      -> after $group/$project: only those top-level names exist
    otherwise                    -> collection schema + fields added by $addFields/$set
    """
    for path in paths:
        if is_forbidden_path(path):
            raise QueryValidationError(f"Field not allowed (protected personal data): {path}")
        if known is None:
            continue
        if RESHAPED in known:
            if path.split(".")[0] not in known:
                visible = sorted(known - {RESHAPED})
                raise QueryValidationError(f"Field '{path}' does not exist at this stage. Available: {visible}")
            continue
        if not is_known_path(collection, path, known):
            valid_roots = sorted({f.split(".")[0] for f in COLLECTION_FIELDS[collection]} | known)
            raise QueryValidationError(
                f"Unknown field '{path}' in collection '{collection}'. Valid top-level fields: {valid_roots}"
            )


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def validate_query(query: dict) -> dict:
    """Returns a normalised copy of the query or raises QueryValidationError."""
    if not isinstance(query, dict):
        raise QueryValidationError("The query must be a JSON object")
    q = copy.deepcopy(query)
    collection = q.get("collection", "admissions")
    if collection not in COLLECTION_FIELDS:
        raise QueryValidationError(f"Collection not allowed: {collection}")
    operation = q.get("operation")
    if operation not in ("aggregate", "find"):
        raise QueryValidationError(f"Unsupported operation: {operation}")
    try:
        limit = int(q.get("limit") or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        limit = DEFAULT_LIMIT
    q["limit"] = max(1, min(limit, MAX_LIMIT))
    q["collection"] = collection

    executable = {k: q.get(k) for k in ("pipeline", "filter", "projection", "sort")}
    _check_operators(executable)

    if operation == "aggregate":
        pipeline = q.get("pipeline") or []
        if not isinstance(pipeline, list) or not all(isinstance(s, dict) and len(s) == 1 for s in pipeline):
            raise QueryValidationError("The pipeline must be a list of single-key stages")
        if len(pipeline) > MAX_STAGES:
            raise QueryValidationError("Pipeline too long")
        _validate_pipeline_fields(collection, pipeline)
        q["pipeline"] = pipeline
    else:
        q["filter"] = q.get("filter") or {}
        if not isinstance(q["filter"], dict):
            raise QueryValidationError("The filter must be an object")
        _check_paths(collection, _filter_paths(q["filter"]), set())
        projection = q.get("projection") or None
        if projection is not None and not isinstance(projection, dict):
            raise QueryValidationError("The projection must be an object")
        if projection:
            _check_paths(collection, [k for k in projection if not k.startswith("$")], set())
            _check_paths(collection, _field_refs(projection), set())
        sort = q.get("sort") or None
        if sort is not None and not isinstance(sort, dict):
            raise QueryValidationError("The sort must be an object")
        if sort:
            _check_paths(collection, sort.keys(), set())
        q["projection"], q["sort"] = projection, sort
    return q


def _validate_pipeline_fields(collection: str, pipeline: list[dict]) -> None:
    """Walks the stages keeping track of which fields exist at each point."""
    known: Optional[set[str]] = set()  # extra fields added by the pipeline so far
    for stage in pipeline:
        name, body = next(iter(stage.items()))
        if name == "$match":
            _check_paths(collection, _filter_paths(body), known)
        elif name in ("$addFields", "$set"):
            _check_paths(collection, _field_refs(body), known)
            if known is not None:
                known |= set(body.keys())
        elif name == "$unwind":
            path = body if isinstance(body, str) else body.get("path", "")
            _check_paths(collection, _field_refs(path), known)
        elif name == "$sort":
            _check_paths(collection, body.keys(), known)
        elif name in ("$group", "$bucket", "$bucketAuto", "$sortByCount"):
            _check_paths(collection, _field_refs(body), known)
            if name == "$group":
                outputs = set(body.keys())
            elif name in ("$bucket", "$bucketAuto"):
                outputs = set((body.get("output") or {}).keys())
            else:
                outputs = set()
            known = _new_shape(outputs | {"_id", "count"})
        elif name == "$project":
            _check_paths(collection, _field_refs(body), known)
            _check_paths(collection, [k for k in body if not k.startswith("$")], None)
            inclusion = any(v not in (0, False) for k, v in body.items() if k != "_id")
            if inclusion:
                known = _new_shape(set(body.keys()) | {"_id"})
        elif name == "$count":
            known = _new_shape({str(body)})
        elif name in ("$replaceRoot", "$replaceWith", "$facet"):
            _check_paths(collection, _field_refs(body), None)
            known = None
        elif name == "$unset":
            continue
        elif name in ("$limit", "$skip", "$sample"):
            continue
        else:
            # Any other stage: still reject forbidden references.
            _check_paths(collection, _field_refs(body), None)


def _new_shape(fields: set[str]) -> set[str]:
    """After a reshaping stage only these top-level names are valid."""
    return {f.split(".")[0] for f in fields} | {RESHAPED}


# --------------------------------------------------------------------------- #
# Privacy enforcement
# --------------------------------------------------------------------------- #
DEFAULT_FIND_EXCLUSIONS = ("services", "medications", "scheduled_surgeries", "etl_run_id")


def _allowed_children(collection: str, parent: str) -> list[str]:
    return [
        f for f in COLLECTION_FIELDS[collection]
        if f.startswith(parent + ".") and not is_forbidden_path(f)
    ]


def enforce_privacy(query: dict) -> dict:
    """Mandatory projection that strips PII regardless of what the LLM generated."""
    q = copy.deepcopy(query)
    if q["collection"] != "admissions":
        return q
    pii = list(PII_PATHS)

    if q["operation"] == "aggregate":
        # Exclusion $project (equivalent to $unset, and supported by every Mongo engine/mock).
        q["pipeline"] = list(q["pipeline"]) + [{"$project": {path: 0 for path in pii}}]
        return q

    projection = q.get("projection")
    if not projection:
        q["projection"] = {p: 0 for p in (*pii, *DEFAULT_FIND_EXCLUSIONS)}
        return q
    is_inclusion = any(v not in (0, False) for k, v in projection.items() if k != "_id")
    if is_inclusion:
        safe: dict[str, Any] = {}
        for key, value in projection.items():
            if key in ("patient", "triage", "diagnosis") and value in (1, True):
                safe.update({child: 1 for child in _allowed_children("admissions", key)})
            elif not is_forbidden_path(key):
                safe[key] = value
        q["projection"] = safe or {"_id": 1}
    else:
        excluded = set(projection)
        for path in pii:
            parent = path.split(".")[0]
            if parent not in excluded:
                projection[path] = 0
        q["projection"] = projection
    return q


def sanitize_results(rows: Any) -> Any:
    """Recursively drops PII keys from results (defence in depth)."""
    if isinstance(rows, list):
        return [sanitize_results(r) for r in rows]
    if isinstance(rows, dict):
        clean = {}
        for key, value in rows.items():
            if key in _PII_LEAF_KEYS:
                continue
            if key in _PII_NESTED and isinstance(value, dict):
                value = {k: v for k, v in value.items() if k not in _PII_NESTED[key]}
            clean[key] = sanitize_results(value)
        return clean
    return rows


def truncate_large_arrays(rows: list[dict], max_items: int = 10) -> list[dict]:
    """Keeps payloads small for the LLM and the browser."""
    def _trim(value: Any) -> Any:
        if isinstance(value, list):
            trimmed = [_trim(v) for v in value[:max_items]]
            if len(value) > max_items:
                trimmed.append(f"... {len(value) - max_items} more")
            return trimmed
        if isinstance(value, dict):
            return {k: _trim(v) for k, v in value.items()}
        return value

    return [_trim(r) for r in rows]


# --------------------------------------------------------------------------- #
# Execution
# --------------------------------------------------------------------------- #
async def execute_query(db, query: dict) -> list[dict]:
    """Validates, enforces privacy and runs a read-only query. Returns sanitised rows."""
    q = enforce_privacy(validate_query(query))
    collection = db[q["collection"]]
    limit = q["limit"]
    max_time = settings.QUERY_MAX_TIME_MS

    if q["operation"] == "aggregate":
        pipeline = q["pipeline"] + [{"$limit": limit}]
        cursor = collection.aggregate(pipeline, maxTimeMS=max_time, allowDiskUse=False)
        rows = await cursor.to_list(length=limit)
    else:
        cursor = collection.find(q["filter"], q["projection"], max_time_ms=max_time)
        if q.get("sort"):
            cursor = cursor.sort(list(q["sort"].items()))
        rows = await cursor.limit(limit).to_list(length=limit)
    return truncate_large_arrays(sanitize_results(rows))
