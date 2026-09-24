"""Builds the NL2MQL system prompt from the REAL data (metadata written by the ETL).

- Reference date = the latest admission in the data (never the server clock), so
  "today", "this month" and "last week" are always resolvable.
- The glossary lists the real distinct values of every categorical field.
- Few-shot examples are generated with real dates in MongoDB Extended JSON.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from bson import json_util

from app.core.i18n import LANGUAGE_NAMES
from app.services.schema_catalog import schema_for_prompt

QUERY_TOOL = {
    "type": "function",
    "function": {
        "name": "run_mongo_query",
        "description": "Run ONE read-only MongoDB query that answers the user's question.",
        "parameters": {
            "type": "object",
            "properties": {
                "collection": {
                    "type": "string",
                    "enum": ["admissions", "inventory", "medication_usage_daily", "service_demand_daily", "bed_capacity"],
                },
                "operation": {"type": "string", "enum": ["aggregate", "find"]},
                "pipeline": {"type": "array", "items": {"type": "object"},
                             "description": "Aggregation stages (operation=aggregate)."},
                "filter": {"type": "object", "description": "Query filter (operation=find)."},
                "projection": {"type": "object"},
                "sort": {"type": "object"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 200},
                "chart": {"type": "string", "enum": ["none", "bar", "line", "pie"],
                          "description": "Best chart for the result table."},
                "explanation": {"type": "string", "description": "One sentence: what the query computes."},
                "out_of_scope": {"type": "boolean",
                                 "description": "true only if the question cannot be answered with this data."},
            },
            "required": ["collection", "operation", "explanation"],
        },
    },
}


def _ejson(value: Any) -> str:
    return json_util.dumps(value, json_options=json_util.RELAXED_JSON_OPTIONS, ensure_ascii=False)


def reference_periods(reference_date: datetime) -> dict[str, datetime]:
    ref = reference_date
    day_start = ref.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = day_start.replace(day=1)
    prev_month_start = (month_start - timedelta(days=1)).replace(day=1)
    return {
        "reference": ref,
        "today_start": day_start,
        "week_start": ref - timedelta(days=7),
        "month_start": month_start,
        "prev_month_start": prev_month_start,
        "last_30_start": ref - timedelta(days=30),
    }


def _few_shots(ref: datetime) -> str:
    p = reference_periods(ref)
    examples = [
        ("¿Cuántas camas de UCI están ocupadas hoy?", {
            "collection": "admissions", "operation": "aggregate",
            "pipeline": [{"$match": {"currently_admitted": True, "bed.group": "UNIDAD DE CUIDADO INTENSIVO"}},
                         {"$group": {"_id": "$bed.group", "occupied_beds": {"$sum": 1}}}],
            "chart": "none", "explanation": "Patients currently in ICU beds at the reference date."}),
        ("¿y en pediatría?  (follow-up of the previous question)", {
            "collection": "admissions", "operation": "aggregate",
            "pipeline": [{"$match": {"currently_admitted": True, "bed.group": "PEDIATRIA"}},
                         {"$group": {"_id": "$bed.group", "occupied_beds": {"$sum": 1}}}],
            "chart": "none", "explanation": "Same question as before, for the paediatrics bed group."}),
        ("¿Cuáles son los medicamentos con menos de 5 días de inventario?", {
            "collection": "inventory", "operation": "find",
            "filter": {"days_of_inventory": {"$lt": 5}},
            "projection": {"_id": 0, "name": 1, "stock": 1, "avg_daily_consumption": 1, "days_of_inventory": 1},
            "sort": {"days_of_inventory": 1}, "limit": 50, "chart": "bar",
            "explanation": "Medications whose stock covers fewer than 5 days of average consumption."}),
        ("¿Cuál es el tiempo de espera promedio en urgencias en la última semana?", {
            "collection": "admissions", "operation": "aggregate",
            "pipeline": [{"$match": {"admission_route": "Urgencias", "wait_minutes": {"$ne": None},
                                     "admission_date": {"$gte": p["week_start"], "$lte": p["reference"]}}},
                         {"$group": {"_id": None, "avg_wait_minutes": {"$avg": "$wait_minutes"},
                                     "patients": {"$sum": 1}}}],
            "chart": "none", "explanation": "Average ER wait (admission to attention) over the last 7 days."}),
        ("¿Qué servicio tiene más pacientes ingresados este mes?", {
            "collection": "admissions", "operation": "aggregate",
            "pipeline": [{"$match": {"admission_date": {"$gte": p["month_start"], "$lte": p["reference"]}}},
                         {"$group": {"_id": "$bed.group", "admissions": {"$sum": 1}}},
                         {"$sort": {"admissions": -1}}, {"$limit": 10}],
            "chart": "bar", "explanation": "Admissions per service (bed group) in the current month."}),
        ("Espera promedio por nivel de triage este mes", {
            "collection": "admissions", "operation": "aggregate",
            "pipeline": [{"$match": {"admission_route": "Urgencias", "wait_minutes": {"$ne": None},
                                     "triage.level": {"$ne": None},
                                     "admission_date": {"$gte": p["month_start"]}}},
                         {"$group": {"_id": "$triage.level", "avg_wait_minutes": {"$avg": "$wait_minutes"},
                                     "patients": {"$sum": 1}}},
                         {"$sort": {"_id": 1}}],
            "chart": "bar", "explanation": "Average ER wait per triage level in the current month."}),
        ("Especialidades más solicitadas en los últimos 30 días", {
            "collection": "service_demand_daily", "operation": "aggregate",
            "pipeline": [{"$match": {"date": {"$gte": p["last_30_start"]}}},
                         {"$group": {"_id": "$specialty", "services": {"$sum": "$lines"}}},
                         {"$sort": {"services": -1}}, {"$limit": 10}],
            "chart": "bar", "explanation": "Services provided per specialty over the last 30 days."}),
        ("Evolución diaria de ingresos en septiembre", {
            "collection": "admissions", "operation": "aggregate",
            "pipeline": [{"$match": {"admission_date": {"$gte": p["month_start"]}}},
                         {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$admission_date"}},
                                     "admissions": {"$sum": 1}}},
                         {"$sort": {"_id": 1}}],
            "chart": "line", "explanation": "Admissions per day in the current month."}),
    ]
    blocks = [f"Q: {q}\nrun_mongo_query arguments: {_ejson(args)}" for q, args in examples]
    return "\n\n".join(blocks)


def build_query_system_prompt(metadata: dict, capacity: list[dict]) -> str:
    ref: datetime = metadata["reference_date"]
    periods = reference_periods(ref)
    values = metadata.get("categorical_values", {})
    glossary_values = "\n".join(f"- {field}: {json.dumps(v, ensure_ascii=False)}" for field, v in values.items())
    beds = {c["group"]: c["beds"] for c in capacity}

    return f"""You are a senior MongoDB analyst for Hospital Susana López de Valencia (Popayán, Colombia).
Translate the user's question (Spanish or English) into ONE read-only MongoDB query by calling
the `run_mongo_query` tool. Never answer in plain text.

REFERENCE DATE (treat it as "today"/"now"; the data ends here): {ref.isoformat()}
- "today/hoy" = from {periods['today_start'].isoformat()} to the reference date
- "last week/última semana" = from {periods['week_start'].isoformat()} to the reference date
- "this month/este mes" = from {periods['month_start'].isoformat()} to the reference date
- "last month/mes pasado" = from {periods['prev_month_start'].isoformat()} to {periods['month_start'].isoformat()}
Data available from {metadata.get('data_start')} to the reference date.

DATES: always write dates as MongoDB Extended JSON: {{"$date": "YYYY-MM-DDTHH:MM:SSZ"}}.
Never compare dates with plain strings.

SCHEMA
{schema_for_prompt()}

REAL VALUES OF CATEGORICAL FIELDS (use them exactly, including missing accents):
{glossary_values}

BED CAPACITY (beds per bed group, capacity proxy): {json.dumps(beds, ensure_ascii=False)}

GLOSSARY
- "servicio"/"service" of a patient = bed.group. UCI/ICU = "UNIDAD DE CUIDADO INTENSIVO";
  UCIN/cuidado intermedio = "UNIDAD DE CUIDADO INTERMEDIO"; cuidado básico/neonatal básico =
  "UNIDAD DE CUIDADO BASICO"; urgencias/ER = "URGENCIAS" (bed group) or admission_route "Urgencias".
- "camas ocupadas"/"occupied beds"/"ocupación actual" = admissions with currently_admitted = true.
  Occupancy % = occupied / beds of the group (see BED CAPACITY).
- "tiempo de espera"/"waiting time" = wait_minutes (already in minutes). ER = admission_route "Urgencias".
- Triage questions use triage.level (1 = most urgent ... 5). NEVER use triage.code for levels.
- Inventory, stock, "días de inventario", shortages => collection `inventory` (field days_of_inventory).
  Its stock is SYNTHETIC; consumption is real.
- Medication consumption/rotation over time => collection `medication_usage_daily` (sum quantity).
- Demand per specialty or service area => collection `service_demand_daily` (sum lines).
- Surgeries performed vs scheduled => sum surgeries_scheduled / surgeries_performed in `admissions`.
- Diagnoses: only the broad ICD-10 chapter (diagnosis.chapter) can be used.

RULES
- Read only. Never use $out, $merge, $lookup, $unionWith, $function, $accumulator, $where,
  $$ROOT or $$CURRENT.
- NEVER select, group by or filter on personal data: patient.name, patient.birth_date,
  patient.patient_id, triage.chief_complaint, diagnosis.code, diagnosis.name. Answer with
  aggregates; for lists of patients use only de-identified fields (bed.group, triage.level,
  patient.sex, patient.age_group, diagnosis.chapter, admission_date).
- Prefer aggregate with $group for counts/averages. Keep results small (limit <= 50 unless a
  full list is requested). Do not return the services/medications arrays.
- Use the conversation history to resolve follow-up questions ("¿y en pediatría?", "and last month?").
- If the question cannot be answered with these collections, call the tool with
  out_of_scope = true, operation "find", collection "bed_capacity".

EXAMPLES
{_few_shots(ref)}
"""


def build_answer_system_prompt(lang: str, reference_date: datetime, capacity: list[dict]) -> str:
    beds = {c["group"]: c["beds"] for c in capacity}
    return f"""You explain hospital operations data to managers and clinical staff.
Write the answer in {LANGUAGE_NAMES.get(lang, 'Spanish')}. Reference date (\"today\"): {reference_date:%Y-%m-%d %H:%M}.
- Answer ONLY in natural language: 1 to 5 short sentences or a short list. Never include JSON,
  code, field names or the database query.
- Use the concrete numbers from the result. Round minutes to one decimal and percentages to one decimal.
- If occupied beds are reported, also give the occupancy % using this capacity: {json.dumps(beds, ensure_ascii=False)}.
- If data comes from the `inventory` collection, mention briefly that stock is simulated.
- If the result is empty, say so clearly and suggest a nearby question.
- When useful, end with ONE concrete operational recommendation (beds, staff, stock or surgery scheduling).
- Never mention names, document numbers, birth dates or specific diagnoses of patients. Do not invent data."""
