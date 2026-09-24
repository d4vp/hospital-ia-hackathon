"""NL2MQL agent powered by OpenAI function calling.

1. Query generation: forced call to the `run_mongo_query` tool (structured arguments,
   no free-form JSON). Arguments are parsed with bson.json_util so {"$date": ...}
   becomes a real datetime.
2. Validation + execution through query_guard (whitelists, PII projection, maxTimeMS).
   If validation or execution fails, the error is sent back to the model ONCE so it
   can fix the query.
3. Answer generation: a second call writes ONLY natural language in the user's
   language from the sanitised rows.

`db` is always the read-only facade (app.db.readonly): the agent has no way to write.
System prompts are built once per ETL run (and language) instead of on every question.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from bson import json_util
from openai import AsyncOpenAI, OpenAIError
from pymongo.errors import PyMongoError

from app.core.config import settings
from app.db.readonly import ReadOnlyViolation
from app.services import cache
from app.services.agent_prompt import QUERY_TOOL, build_answer_system_prompt, build_query_system_prompt
from app.services.data_repository import dataset_key
from app.services.query_guard import QueryValidationError, execute_query

logger = logging.getLogger("agent")

_client: Optional[AsyncOpenAI] = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY, timeout=settings.OPENAI_TIMEOUT_SECONDS, max_retries=1)
    return _client


_prompt_cache = cache.register(maxsize=16, ttl=settings.CACHE_TTL_SECONDS)


def _cached_prompt(key: tuple, build) -> str:
    prompt = _prompt_cache.get(key)
    if cache.is_missing(prompt):
        prompt = build()
        _prompt_cache.set(key, prompt)
    return prompt


def query_system_prompt(metadata: dict, capacity: list[dict]) -> str:
    return _cached_prompt(("query", dataset_key(metadata)),
                          lambda: build_query_system_prompt(metadata, capacity))


def answer_system_prompt(lang: str, metadata: dict, capacity: list[dict]) -> str:
    return _cached_prompt(("answer", dataset_key(metadata), lang),
                          lambda: build_answer_system_prompt(lang, metadata["reference_date"], capacity))


class LLMUnavailableError(RuntimeError):
    """OpenAI is not configured or failed: the caller switches to Plan B."""


@dataclass
class AgentResult:
    answer: str
    rows: list[dict] = field(default_factory=list)
    query: Optional[dict] = None
    chart: Optional[str] = None
    explanation: str = ""
    retried: bool = False
    out_of_scope: bool = False
    errors: list[str] = field(default_factory=list)


def _default(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _history_messages(history: list[dict]) -> list[dict]:
    messages: list[dict] = []
    for turn in history:
        messages.append({"role": "user", "content": turn["question"]})
        summary = f"[query: {turn.get('explanation') or turn.get('intent') or '-'}] {turn['answer']}"
        messages.append({"role": "assistant", "content": summary[:1500]})
    return messages


async def _call_tool(messages: list[dict]) -> tuple[dict, Any]:
    try:
        response = await get_client().chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=messages,
            tools=[QUERY_TOOL],
            tool_choice={"type": "function", "function": {"name": "run_mongo_query"}},
            temperature=0,
        )
    except OpenAIError as exc:
        raise LLMUnavailableError(str(exc)) from exc
    message = response.choices[0].message
    if not message.tool_calls:
        raise LLMUnavailableError("The model did not call the query tool")
    call = message.tool_calls[0]
    try:
        arguments = json_util.loads(call.function.arguments)
    except (ValueError, TypeError) as exc:
        arguments = {"_parse_error": str(exc)}
    return arguments, message


async def generate_and_run(db, question: str, history: list[dict], metadata: dict, capacity: list[dict]) -> AgentResult:
    if not settings.openai_enabled:
        raise LLMUnavailableError("OPENAI_API_KEY is not configured")

    messages = [{"role": "system", "content": query_system_prompt(metadata, capacity)}]
    messages += _history_messages(history)
    messages.append({"role": "user", "content": question})

    errors: list[str] = []
    for attempt in range(2):
        arguments, assistant_message = await _call_tool(messages)
        if arguments.get("out_of_scope"):
            return AgentResult(answer="", out_of_scope=True, query=arguments, explanation=arguments.get("explanation", ""))
        try:
            if "_parse_error" in arguments:
                raise QueryValidationError(f"Invalid JSON arguments: {arguments['_parse_error']}")
            rows = await execute_query(db, arguments)
            chart = arguments.get("chart")
            return AgentResult(answer="", rows=rows, query=arguments, chart=None if chart == "none" else chart,
                               explanation=arguments.get("explanation", ""), retried=attempt > 0, errors=errors)
        except (QueryValidationError, ReadOnlyViolation, PyMongoError) as exc:
            errors.append(str(exc))
            logger.info("agent_query_rejected", extra={"data": {"attempt": attempt, "error": str(exc)}})
            if attempt == 1:
                raise
            call = assistant_message.tool_calls[0]
            messages.append({
                "role": "assistant", "content": None,
                "tool_calls": [{"id": call.id, "type": "function",
                                "function": {"name": call.function.name, "arguments": call.function.arguments}}],
            })
            messages.append({
                "role": "tool", "tool_call_id": call.id,
                "content": f"ERROR: {exc}. Fix the query using only valid fields and call run_mongo_query again.",
            })
    raise LLMUnavailableError("unreachable")


async def write_answer(question: str, result: AgentResult, lang: str, metadata: dict, capacity: list[dict]) -> str:
    rows_json = json.dumps(result.rows[:40], default=_default, ensure_ascii=False)[:12000]
    try:
        response = await get_client().chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": answer_system_prompt(lang, metadata, capacity)},
                {"role": "user", "content": (
                    f"Question: {question}\nWhat the query computed: {result.explanation}\n"
                    f"Collection: {(result.query or {}).get('collection')}\n"
                    f"Rows returned: {len(result.rows)}\nResult rows (JSON): {rows_json}"
                )},
            ],
            temperature=0.2,
            max_tokens=settings.CHAT_MAX_ANSWER_TOKENS,
        )
    except OpenAIError as exc:
        raise LLMUnavailableError(str(exc)) from exc
    return (response.choices[0].message.content or "").strip()
