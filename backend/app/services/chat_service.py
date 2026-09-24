"""Orchestrates one chat turn: input guard -> memory -> LLM agent (or Plan B) -> table/chart -> log.

Response contract (the frontend relies on it):
{
  "answer": "natural-language text only",
  "table": {"columns": [...], "rows": [...]} | null,
  "chart": {"type": "bar|line|pie", "x": "col", "y": "col"} | null,
  "engine": "llm" | "fallback" | "guard",
  "conversation_id": "...",
  "reference_date": "ISO date" | null,
  "cached": bool
}
The generated database query is NEVER returned to any role (admin included): it is only
written to the server-side audit log (`agent_logs`).

Latency
- The input guard runs first: blocked messages cost no database or LLM call.
- Metadata and conversation history are read concurrently; bed capacity is cached per ETL run.
- Context-free questions (no conversation history) are answered from an in-memory cache
  keyed by (ETL run, language, normalised question) for CHAT_CACHE_TTL_SECONDS.
- The conversation turn and the audit log are written concurrently.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from bson import json_util
from pymongo.errors import PyMongoError

from app.core.config import COLLECTIONS, settings
from app.core.i18n import fmt_number, t
from app.db.mongo import get_readonly_db
from app.db.readonly import ReadOnlyViolation
from app.services import cache, fallback_agent, mongo_agent
from app.services.data_repository import DatasetNotLoadedError, get_capacity_docs, get_metadata
from app.services.query_guard import QueryValidationError, screen_question
from app.services.text_utils import normalize_text

logger = logging.getLogger("agent")
MAX_TABLE_ROWS = 200
_answer_cache = cache.register(maxsize=512, ttl=settings.CHAT_CACHE_TTL_SECONDS)


# --------------------------------------------------------------------------- #
# Result shaping
# --------------------------------------------------------------------------- #
def _scalar(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float):
        return round(value, 2)
    if value is None or isinstance(value, (int, str, bool)):
        return value
    return str(value)  # ObjectId, Decimal128, ...


def _flatten(row: dict, prefix: str = "") -> dict:
    flat: dict[str, Any] = {}
    for key, value in row.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            flat.update(_flatten(value, name + "."))
        elif isinstance(value, list):
            continue  # arrays are not shown in the table
        else:
            flat[name] = _scalar(value)
    return flat


def build_table(rows: list[dict]) -> Optional[dict]:
    if not rows:
        return None
    flat_rows = [_flatten(r) for r in rows[:MAX_TABLE_ROWS]]
    for r in flat_rows:
        if "_id" in r and r["_id"] is None:
            r.pop("_id")
    columns: list[str] = []
    for r in flat_rows:
        columns.extend(c for c in r if c not in columns)
    if not columns:
        return None
    return {"columns": columns, "rows": [[r.get(c) for c in columns] for r in flat_rows]}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _looks_like_date(value: Any) -> bool:
    return isinstance(value, str) and len(value) >= 7 and value[:4].isdigit() and value[4] == "-"


def build_chart(table: Optional[dict], hint: Optional[Any]) -> Optional[dict]:
    if not table or len(table["rows"]) < 2:
        return None
    if isinstance(hint, dict) and hint.get("x") in table["columns"] and hint.get("y") in table["columns"]:
        return hint
    columns, first = table["columns"], table["rows"][0]
    numeric = [c for c, v in zip(columns, first) if _is_number(v) and c != "_id"]
    categorical = [c for c, v in zip(columns, first) if not _is_number(v) or c == "_id"]
    if not numeric or not categorical:
        return None
    x, y = categorical[0], numeric[0]
    x_value = first[columns.index(x)]
    kind = hint if hint in ("bar", "line", "pie") else ("line" if _looks_like_date(x_value) else "bar")
    if kind == "bar" and len(table["rows"]) > 40:
        return None
    return {"type": kind, "x": x, "y": y}


def _generic_answer(rows: list[dict], lang: str) -> str:
    table = build_table(rows)
    if not table:
        return t("no_data", lang)
    if len(table["rows"]) == 1:
        parts = [f"{c}: {fmt_number(v, lang) if _is_number(v) else v}" for c, v in zip(table["columns"], table["rows"][0])]
        return "; ".join(parts)
    return ("Resultados" if lang == "es" else "Results") + f": {len(table['rows'])}"


# --------------------------------------------------------------------------- #
# Conversation memory
# --------------------------------------------------------------------------- #
async def _load_history(db, conversation_id: Optional[str], user_id: str) -> list[dict]:
    if not conversation_id:
        return []
    doc = await db[COLLECTIONS["conversations"]].find_one({"_id": conversation_id, "user_id": user_id})
    return (doc or {}).get("turns", [])[-settings.CHAT_MEMORY_TURNS:]


async def _save_turn(db, conversation_id: str, user_id: str, turn: dict) -> None:
    await db[COLLECTIONS["conversations"]].update_one(
        {"_id": conversation_id, "user_id": user_id},
        {"$push": {"turns": {"$each": [turn], "$slice": -20}},
         "$set": {"updated_at": datetime.now(timezone.utc)}},
        upsert=True,
    )


async def _log(db, entry: dict) -> None:
    logger.info("agent_query", extra={"data": entry})
    try:
        await db[COLLECTIONS["agent_logs"]].insert_one({**entry, "at": datetime.now(timezone.utc)})
    except PyMongoError:
        logger.warning("agent_log_insert_failed")


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #
def _response(answer: str, table: Optional[dict], chart: Optional[dict], engine: str, conversation_id: str,
              reference_date: Optional[datetime], cached: bool = False) -> dict:
    return {
        "answer": answer, "table": table, "chart": chart, "engine": engine,
        "conversation_id": conversation_id,
        "reference_date": reference_date.isoformat() if reference_date else None,
        "cached": cached,
    }


async def _persist(db, conversation_id: str, user_id: str, question: str, answer: str, turn: dict, log: dict) -> None:
    now = datetime.now(timezone.utc)
    await asyncio.gather(
        _save_turn(db, conversation_id, user_id, {"question": question, "answer": answer, **turn, "at": now}),
        _log(db, {"user_id": user_id, "question": question, **log}),
    )


async def ask(db, user: dict, question: str, conversation_id: Optional[str], lang: str) -> dict:
    conversation_id = conversation_id or uuid.uuid4().hex
    user_id = str(user["_id"])

    # 0) Write commands / prompt injection: rejected before touching the LLM or the database.
    blocked = screen_question(question)
    if blocked:
        logger.warning("agent_input_blocked", extra={"data": {"user_id": user_id, "reason": blocked}})
        return _response(t(f"guard_{blocked}", lang), None, None, "guard", conversation_id, None)

    metadata, history = await asyncio.gather(get_metadata(db), _load_history(db, conversation_id, user_id))
    if not metadata:
        raise DatasetNotLoadedError("No data loaded yet")
    run_id = str(metadata.get("etl_run_id"))
    reference_date: datetime = metadata["reference_date"]
    started = time.perf_counter()

    # 1) Context-free question already answered for this dataset: serve it from memory.
    cache_key = None if history else (run_id, lang, normalize_text(question))
    hit = _answer_cache.get(cache_key) if cache_key else None
    if hit is not None and not cache.is_missing(hit):
        duration_ms = round((time.perf_counter() - started) * 1000)
        await _persist(db, conversation_id, user_id, question, hit["answer"], hit["turn"],
                       {"engine": hit["engine"], "query": hit["query_log"], "duration_ms": duration_ms,
                        "rows": hit["rows"], "success": True, "retried": False, "errors": [], "cached": True})
        return _response(hit["answer"], hit["table"], hit["chart"], hit["engine"], conversation_id,
                         reference_date, cached=True)

    # 2) Agent queries run on the read-only handle (no write method exists on it).
    readonly_db = get_readonly_db(db)
    capacity = await get_capacity_docs(db, run_id)
    last_turn = history[-1] if history else None
    engine, errors = "llm", []
    rows: list[dict] = []
    chart_hint: Any = None
    queries: list[dict] = []
    explanation, intent, params, retried, degraded = "", None, {}, False, False

    try:
        result = await mongo_agent.generate_and_run(readonly_db, question, history, metadata, capacity)
        queries, explanation, retried, errors = [result.query], result.explanation, result.retried, result.errors
        if result.out_of_scope:
            answer = t("out_of_scope", lang)
        else:
            rows, chart_hint = result.rows, result.chart
            try:
                answer = await mongo_agent.write_answer(question, result, lang, metadata, capacity)
            except mongo_agent.LLMUnavailableError as exc:
                errors.append(f"answer: {exc}")
                answer, degraded = _generic_answer(rows, lang), True
    except (mongo_agent.LLMUnavailableError, QueryValidationError, ReadOnlyViolation, PyMongoError) as exc:
        errors.append(str(exc))
        engine = "fallback"
        fb = await fallback_agent.answer(readonly_db, question, lang, metadata, last_turn)
        rows, chart_hint, queries, intent, params = fb.rows, fb.chart, fb.queries, fb.intent, fb.params
        answer = fb.answer if fb.intent is None else f"{fb.answer}\n\n{t('fallback_note', lang)}"
        explanation = f"fallback:{fb.intent}"

    table = build_table(rows)
    chart = build_chart(table, chart_hint)
    duration_ms = round((time.perf_counter() - started) * 1000)
    success = not (engine == "fallback" and intent is None)
    turn = {"intent": intent, "params": params, "explanation": explanation, "engine": engine}
    # String: "$" keys cannot be stored as field names. Server-side audit only, never returned.
    query_log = json_util.dumps(queries, json_options=json_util.RELAXED_JSON_OPTIONS)

    await _persist(db, conversation_id, user_id, question, answer, turn,
                   {"engine": engine, "query": query_log, "duration_ms": duration_ms, "rows": len(rows),
                    "success": success, "retried": retried, "errors": errors})

    # Cache only complete answers: LLM answers, or Plan B when the LLM is not configured at all
    # (a Plan B answer caused by a transient OpenAI failure must not hide the LLM once it recovers).
    cacheable = success and not degraded and (engine == "llm" or not settings.openai_enabled)
    if cache_key and cacheable:
        _answer_cache.set(cache_key, {"answer": answer, "table": table, "chart": chart, "engine": engine,
                                      "turn": turn, "query_log": query_log, "rows": len(rows)})
    return _response(answer, table, chart, engine, conversation_id, reference_date)
