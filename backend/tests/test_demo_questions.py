"""One test per official demo question (Plan B engine: deterministic, no OpenAI needed).

The LLM path uses the same collections and the same guard; these tests prove that the
data model, the query guard and the predefined queries return the expected answers.
"""
import pytest

from app.core.config import COLLECTIONS
from app.services import fallback_agent


async def _ask(db, question, lang="es", last_turn=None):
    metadata = await db[COLLECTIONS["metadata"]].find_one({"_id": "dataset"})
    return await fallback_agent.answer(db, question, lang, metadata, last_turn)


async def test_q1_icu_beds_occupied_today(db):
    result = await _ask(db, "¿Cuántas camas de UCI están ocupadas hoy?")
    assert result.intent == "occupancy"
    row = result.rows[0]
    assert row["bed_group"] == "UNIDAD DE CUIDADO INTENSIVO"
    assert row["occupied"] == 2 and row["beds"] == 3
    assert "2 camas ocupadas de 3" in result.answer


async def test_q2_medications_under_5_days_of_inventory(db):
    result = await _ask(db, "¿Cuáles son los medicamentos con menos de 5 días de inventario?")
    assert result.intent == "low_inventory"
    assert [r["name"] for r in result.rows] == ["AMOXICILINA 500 MG"]
    assert "simulado" in result.answer


async def test_q3_average_er_wait_last_week(db):
    result = await _ask(db, "¿Cuál es el tiempo de espera promedio en urgencias en la última semana?")
    assert result.intent == "er_wait"
    assert result.rows[0]["patients"] == 2
    assert result.rows[0]["avg_wait_minutes"] == pytest.approx(60.0)
    assert "60,0 minutos" in result.answer


async def test_q4_service_with_most_admissions_this_month(db):
    result = await _ask(db, "¿Qué servicio tiene más pacientes ingresados este mes?")
    assert result.intent == "top_service"
    assert result.rows[0] == {"bed_group": "UNIDAD DE CUIDADO INTENSIVO", "admissions": 3}


async def test_follow_up_reuses_previous_intent(db):
    first = await _ask(db, "¿Cuántas camas de UCI están ocupadas hoy?")
    follow = await _ask(db, "¿y en pediatría?", last_turn={"intent": first.intent, "params": first.params})
    assert follow.intent == "occupancy"
    assert follow.rows[0]["bed_group"] == "PEDIATRIA" and follow.rows[0]["occupied"] == 1


async def test_english_question(db):
    result = await _ask(db, "How many ICU beds are occupied today?", lang="en")
    assert "2 occupied beds out of 3" in result.answer
