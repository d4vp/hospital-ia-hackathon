"""LLM path of the agent with a fake OpenAI client (no network): happy path, retries,
out-of-scope, failures -> Plan B, and an LLM that tries to write (blocked, nothing written)."""
import json
from types import SimpleNamespace

import pytest
from openai import OpenAIError

from app.core.config import COLLECTIONS
from app.services import chat_service, mongo_agent

ICU_QUERY = {"collection": "admissions", "operation": "aggregate", "chart": "bar",
             "pipeline": [{"$match": {"currently_admitted": True}},
                          {"$group": {"_id": "$bed.group", "occupied_beds": {"$sum": 1}}}],
             "explanation": "Occupied beds per group"}


def tool_call(arguments) -> SimpleNamespace:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    call = SimpleNamespace(id="call_1", function=SimpleNamespace(name="run_mongo_query", arguments=raw))
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call], content=None))])


def text(content: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=None, content=content))])


class FakeOpenAI:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


@pytest.fixture
def llm(monkeypatch):
    def install(*responses) -> FakeOpenAI:
        client = FakeOpenAI(*responses)
        monkeypatch.setattr(mongo_agent.settings, "OPENAI_API_KEY", "sk-test")
        monkeypatch.setattr(mongo_agent, "get_client", lambda: client)
        return client
    return install


USER = {"_id": "u1", "role": "user"}


async def test_llm_answer_is_cached(db, llm):
    client = llm(tool_call(ICU_QUERY), text("La UCI tiene 2 camas ocupadas."))
    first = await chat_service.ask(db, USER, "¿Camas ocupadas por servicio?", None, "es")
    assert first["engine"] == "llm" and first["answer"].startswith("La UCI") and first["chart"]["type"] == "bar"
    assert "REFERENCE DATE" in client.calls[0]["messages"][0]["content"]  # system prompt built from real data
    assert client.calls[1]["max_tokens"] == mongo_agent.settings.CHAT_MAX_ANSWER_TOKENS
    second = await chat_service.ask(db, USER, "¿camas ocupadas por servicio?", None, "es")
    assert second["cached"] is True and len(client.calls) == 2  # no extra OpenAI call


async def test_invalid_query_is_retried_once(db, llm):
    bad = {**ICU_QUERY, "pipeline": [{"$group": {"_id": "$grupo_cama"}}]}
    client = llm(tool_call(bad), tool_call(ICU_QUERY), text("Respuesta"))
    response = await chat_service.ask(db, USER, "camas", None, "es")
    assert response["engine"] == "llm" and len(client.calls) == 3
    assert client.calls[1]["messages"][-1]["role"] == "tool"  # the error was fed back to the model


async def test_llm_trying_to_write_is_blocked_and_falls_back(db, llm):
    write = {**ICU_QUERY, "pipeline": [{"$match": {}}, {"$out": "admissions_backup"}]}
    update = {**ICU_QUERY, "pipeline": [{"$set": {"currently_admitted": False}}]}
    llm(tool_call(write), tool_call(update))
    response = await chat_service.ask(db, USER, "¿Cuántas camas de UCI están ocupadas hoy?", None, "es")
    assert response["engine"] == "fallback"
    assert "admissions_backup" not in await db.list_collection_names()
    assert await db[COLLECTIONS["admissions"]].count_documents({"currently_admitted": True}) >= 1


@pytest.mark.parametrize("responses", [
    [OpenAIError("quota")],
    [text("sin herramienta")],
    [tool_call("{not json"), tool_call("{still not json")],
])
async def test_llm_failures_use_plan_b(db, llm, responses):
    llm(*responses)
    response = await chat_service.ask(db, USER, "¿Cuántas camas de UCI están ocupadas hoy?", None, "es")
    assert response["engine"] == "fallback" and "2 camas ocupadas de 3" in response["answer"]


async def test_out_of_scope(db, llm):
    llm(tool_call({"collection": "bed_capacity", "operation": "find", "out_of_scope": True, "explanation": "-"}))
    response = await chat_service.ask(db, USER, "¿Quién ganó el mundial?", None, "es")
    assert "fuera del alcance" in response["answer"]


async def test_answer_failure_gives_generic_answer_not_cached(db, llm):
    single = {**ICU_QUERY, "pipeline": [{"$match": {"currently_admitted": True}}, {"$count": "occupied"}]}
    client = llm(tool_call(single), OpenAIError("timeout"), tool_call(single), text("ok"))
    first = await chat_service.ask(db, USER, "total ocupadas", None, "en")
    assert first["engine"] == "llm" and first["answer"].startswith("occupied")
    second = await chat_service.ask(db, USER, "total ocupadas", None, "en")
    assert second["cached"] is False and len(client.calls) == 4


async def test_follow_up_uses_history(db, llm):
    client = llm(tool_call(ICU_QUERY), text("Primera"), tool_call(ICU_QUERY), text("Segunda"))
    first = await chat_service.ask(db, USER, "camas UCI", None, "es")
    await chat_service.ask(db, USER, "¿y en pediatría?", first["conversation_id"], "es")
    history = [m["content"] for m in client.calls[2]["messages"] if m["role"] in ("user", "assistant")]
    assert history[0] == "camas UCI" and "Primera" in history[1]


async def test_llm_disabled_raises(monkeypatch):
    monkeypatch.setattr(mongo_agent.settings, "OPENAI_API_KEY", "")
    with pytest.raises(mongo_agent.LLMUnavailableError):
        await mongo_agent.generate_and_run(None, "q", [], {}, [])
