"""Plan B: keyword intents -> predefined MongoDB queries -> templated answers.

Used automatically when there is no OPENAI_API_KEY or the OpenAI call fails.
Predefined queries still go through query_guard (same validation and PII rules).
Supports simple follow-ups: "¿y en pediatría?" reuses the previous intent with a new
bed group; "¿y el mes pasado?" reuses it with a new period.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional

from app.core.i18n import fmt_date, fmt_number, t
from app.services.agent_prompt import reference_periods
from app.services.query_guard import execute_query
from app.services.text_utils import normalize_text

# --------------------------------------------------------------------------- #
# Vocabulary (accent-free, lower-case)
# --------------------------------------------------------------------------- #
BED_GROUP_SYNONYMS: list[tuple[str, str]] = [
    (r"\b(uci|icu|cuidados? intensivos?|intensive care)\b", "UNIDAD DE CUIDADO INTENSIVO"),
    (r"\b(ucin|intermedi[oa]s?|intermediate care)\b", "UNIDAD DE CUIDADO INTERMEDIO"),
    (r"\b(cuidado basico|basic care|neonatal basic)\b", "UNIDAD DE CUIDADO BASICO"),
    (r"\b(pediatri[ac]\w*|paediatric\w*|pediatric\w*|ninos|children)\b", "PEDIATRIA"),
    (r"\b(gineco\w*|obstetri\w*|maternidad|maternity)\b", "GINECO OBSTRETICIA"),
    (r"\b(sala de partos|partos|delivery room)\b", "SALA PARTOS"),
    (r"\b(recuperacion|recovery)\b", "RECUPERACION"),
    (r"\b(hospitalizacion|ward|inpatient)\b", "HOSPITALIZACION"),
    (r"\b(urgencias|emergency|emergencias|er)\b", "URGENCIAS"),
]

INTENTS: list[tuple[str, list[str]]] = [
    # (intent, list of regexes that must ALL match)
    ("low_inventory", [r"(inventario|stock|desabastec|agot|existencia|inventory|shortage|stock-?out)"]),
    ("wait_by_triage", [r"(espera|wait|demora)", r"triage"]),
    ("er_wait", [r"(espera|wait|demora|tiempo de atencion)"]),
    ("top_service", [r"(servicio|service|area)", r"(mas|most|mayor|top)", r"(ingres|admi|pacientes|patients)"]),
    ("least_meds", [r"(medicament|medication|insumo|drug)", r"(menor|menos|least|lowest|baja rotacion|low turnover)"]),
    ("top_meds", [r"(medicament|medication|insumo|drug)", r"(mayor|mas|most|top|consum|rotacion|used)"]),
    ("surgeries", [r"(cirug|quirof|surger|operating|operacion)"]),
    ("specialty_demand", [r"(especialidad|specialt|demanda|demand)"]),
    ("occupancy", [r"(ocupad|ocupacion|occupied|occupancy|camas|beds|censo|census)"]),
]

DEFAULT_PERIOD = {
    "er_wait": "week", "top_service": "month", "top_meds": "last30", "least_meds": "last30",
    "surgeries": "last30", "specialty_demand": "last30", "wait_by_triage": "last30",
}


@dataclass
class FallbackResult:
    intent: Optional[str]
    params: dict[str, Any]
    answer: str
    rows: list[dict] = field(default_factory=list)
    chart: Optional[dict] = None
    queries: list[dict] = field(default_factory=list)


def detect_intent(text: str) -> Optional[str]:
    norm = normalize_text(text)
    for intent, patterns in INTENTS:
        if all(re.search(p, norm) for p in patterns):
            return intent
    return None


def detect_params(text: str) -> dict[str, Any]:
    norm = normalize_text(text)
    params: dict[str, Any] = {}
    for pattern, group in BED_GROUP_SYNONYMS:
        if re.search(pattern, norm):
            params["bed_group"] = group
            break
    if re.search(r"(mes pasado|mes anterior|last month|previous month)", norm):
        params["period"] = "prev_month"
    elif re.search(r"\b(hoy|today|actual|ahora|now)\b", norm):
        params["period"] = "today"
    elif re.search(r"(semana|week|7 dias|7 days)", norm):
        params["period"] = "week"
    elif re.search(r"(este mes|this month|\bmes\b|\bmonth\b)", norm):
        params["period"] = "month"
    elif re.search(r"(30 dias|30 days|ultimo mes|last 30)", norm):
        params["period"] = "last30"
    elif re.search(r"(todo el periodo|historico|total|all time|whole period)", norm):
        params["period"] = "all"
    days = re.search(r"(?:menos de|less than|under|<)\s*(\d+(?:[.,]\d+)?)\s*(?:dias|days)", norm)
    if days:
        params["days"] = float(days.group(1).replace(",", "."))
    top = re.search(r"\b(?:top|los|las|the)\s+(\d{1,2})\b", norm)
    if top:
        params["top"] = int(top.group(1))
    return params


def resolve_period(period: str, reference: datetime, data_start: Optional[datetime]) -> tuple[datetime, datetime]:
    p = reference_periods(reference)
    if period == "today":
        return p["today_start"], reference
    if period == "week":
        return p["week_start"], reference
    if period == "month":
        return p["month_start"], reference
    if period == "prev_month":
        return p["prev_month_start"], p["month_start"] - timedelta(seconds=1)
    if period == "all":
        return data_start or reference - timedelta(days=365), reference
    return p["last_30_start"], reference


def _lines(rows: list[dict], label: str, value: str, lang: str, suffix: str = "", limit: int = 10) -> str:
    return "\n".join(f"- {r.get(label)}: {fmt_number(r.get(value), lang, 1 if isinstance(r.get(value), float) else 0)}{suffix}"
                     for r in rows[:limit])


async def answer(db, question: str, lang: str, metadata: dict, last_turn: Optional[dict] = None) -> FallbackResult:
    reference: datetime = metadata["reference_date"]
    data_start: Optional[datetime] = metadata.get("data_start")
    intent = detect_intent(question)
    params = detect_params(question)
    if intent is None and last_turn and last_turn.get("intent") and params:
        intent = last_turn["intent"]  # follow-up: reuse the previous intent with the new parameters
        params = {**(last_turn.get("params") or {}), **params}
    if intent is None:
        return FallbackResult(None, params, t("no_match", lang))

    handler = HANDLERS[intent]
    period = params.get("period") or DEFAULT_PERIOD.get(intent, "last30")
    start, end = resolve_period(period, reference, data_start)
    params["period"] = period
    result = await handler(db, params, lang, start, end, reference)
    result.intent, result.params = intent, params
    return result


# --------------------------------------------------------------------------- #
# Handlers (each returns a FallbackResult)
# --------------------------------------------------------------------------- #
def _period_label(start: datetime, end: datetime, lang: str) -> str:
    return f"{fmt_date(start, lang)} – {fmt_date(end, lang)}"


async def _occupancy(db, params, lang, start, end, reference) -> FallbackResult:
    group = params.get("bed_group")
    match: dict[str, Any] = {"currently_admitted": True}
    if group:
        match["bed.group"] = group
    q_occ = {"collection": "admissions", "operation": "aggregate", "limit": 50, "pipeline": [
        {"$match": match}, {"$group": {"_id": "$bed.group", "occupied": {"$sum": 1}}}, {"$sort": {"occupied": -1}}]}
    q_cap = {"collection": "bed_capacity", "operation": "find", "filter": {"_id": group} if group else {}, "limit": 50}
    occupied = {r["_id"]: r["occupied"] for r in await execute_query(db, q_occ)}
    capacity = await execute_query(db, q_cap)
    rows = []
    for cap in capacity:
        occ = occupied.get(cap["_id"], 0)
        pct = round(occ / cap["beds"] * 100, 1) if cap.get("beds") else None
        rows.append({"bed_group": cap["_id"], "occupied": occ, "beds": cap["beds"], "occupancy_pct": pct})
    rows.sort(key=lambda r: -(r["occupancy_pct"] or 0))
    date = fmt_date(reference, lang)
    if group and rows:
        r = rows[0]
        text = t("fb_occupancy_group", lang, date=date, group=group, occupied=r["occupied"], beds=r["beds"],
                 pct=fmt_number(r["occupancy_pct"], lang))
    elif rows:
        lines = "\n".join(f"- {r['bed_group']}: {r['occupied']}/{r['beds']} ({fmt_number(r['occupancy_pct'], lang)}%)" for r in rows)
        text = t("fb_occupancy_all", lang, date=date, lines=lines)
    else:
        text = t("no_data", lang)
    return FallbackResult(None, params, text, rows, {"type": "bar", "x": "bed_group", "y": "occupancy_pct"}, [q_occ, q_cap])


async def _low_inventory(db, params, lang, start, end, reference) -> FallbackResult:
    days = float(params.get("days") or 5)
    query = {"collection": "inventory", "operation": "find", "filter": {"days_of_inventory": {"$lt": days}},
             "projection": {"_id": 0, "code": 1, "name": 1, "stock": 1, "avg_daily_consumption": 1, "days_of_inventory": 1},
             "sort": {"days_of_inventory": 1, "avg_daily_consumption": -1}, "limit": 200}
    rows = await execute_query(db, query)
    if not rows:
        return FallbackResult(None, params, t("fb_low_inventory_none", lang, days=fmt_number(days, lang, 0)), [], None, [query])
    unit = t("unit_days", lang)
    lines = "\n".join(
        f"- {str(r['name'])[:70]}: {fmt_number(r['days_of_inventory'], lang)} {unit} (stock {r['stock']})" for r in rows[:10]
    )
    text = t("fb_low_inventory", lang, count=len(rows), days=fmt_number(days, lang, 0), lines=lines)
    return FallbackResult(None, params, text, rows, {"type": "bar", "x": "name", "y": "days_of_inventory"}, [query])


async def _er_wait(db, params, lang, start, end, reference) -> FallbackResult:
    query = {"collection": "admissions", "operation": "aggregate", "pipeline": [
        {"$match": {"admission_route": "Urgencias", "wait_minutes": {"$ne": None},
                    "admission_date": {"$gte": start, "$lte": end}}},
        {"$group": {"_id": None, "avg_wait_minutes": {"$avg": "$wait_minutes"}, "patients": {"$sum": 1}}}]}
    rows = await execute_query(db, query)
    if not rows or not rows[0].get("patients"):
        return FallbackResult(None, params, t("no_data", lang), [], None, [query])
    r = rows[0]
    text = t("fb_er_wait", lang, start=fmt_date(start, lang), end=fmt_date(end, lang),
             mean=fmt_number(r["avg_wait_minutes"], lang), count=r["patients"])
    clean = [{"avg_wait_minutes": round(r["avg_wait_minutes"], 1), "patients": r["patients"]}]
    return FallbackResult(None, params, text, clean, None, [query])


async def _top_service(db, params, lang, start, end, reference) -> FallbackResult:
    query = {"collection": "admissions", "operation": "aggregate", "pipeline": [
        {"$match": {"admission_date": {"$gte": start, "$lte": end}}},
        {"$group": {"_id": "$bed.group", "admissions": {"$sum": 1}}},
        {"$sort": {"admissions": -1}}, {"$limit": 10}]}
    rows = [{"bed_group": r["_id"], "admissions": r["admissions"]} for r in await execute_query(db, query)]
    if not rows:
        return FallbackResult(None, params, t("no_data", lang), [], None, [query])
    others = ", ".join(f"{r['bed_group']} ({r['admissions']})" for r in rows[1:4])
    text = t("fb_top_service", lang, period=_period_label(start, end, lang), group=rows[0]["bed_group"],
             count=rows[0]["admissions"], others=others or "—")
    return FallbackResult(None, params, text, rows, {"type": "bar", "x": "bed_group", "y": "admissions"}, [query])


async def _meds(db, params, lang, start, end, reference, lowest: bool) -> FallbackResult:
    top = int(params.get("top") or 10)
    query = {"collection": "medication_usage_daily", "operation": "aggregate", "pipeline": [
        {"$match": {"date": {"$gte": start, "$lte": end}}},
        {"$group": {"_id": "$name", "quantity": {"$sum": "$quantity"}}},
        {"$match": {"quantity": {"$gt": 0}}},
        {"$sort": {"quantity": 1 if lowest else -1}}, {"$limit": top}]}
    rows = [{"medication": r["_id"], "quantity": r["quantity"]} for r in await execute_query(db, query)]
    if not rows:
        return FallbackResult(None, params, t("no_data", lang), [], None, [query])
    key = "fb_least_meds" if lowest else "fb_top_meds"
    text = t(key, lang, period=_period_label(start, end, lang), lines=_lines(rows, "medication", "quantity", lang))
    return FallbackResult(None, params, text, rows, {"type": "bar", "x": "medication", "y": "quantity"}, [query])


async def _top_meds(db, params, lang, start, end, reference) -> FallbackResult:
    return await _meds(db, params, lang, start, end, reference, lowest=False)


async def _least_meds(db, params, lang, start, end, reference) -> FallbackResult:
    return await _meds(db, params, lang, start, end, reference, lowest=True)


async def _surgeries(db, params, lang, start, end, reference) -> FallbackResult:
    query = {"collection": "admissions", "operation": "aggregate", "pipeline": [
        {"$match": {"admission_date": {"$gte": start, "$lte": end}}},
        {"$group": {"_id": None, "scheduled": {"$sum": "$surgeries_scheduled"},
                    "performed": {"$sum": "$surgeries_performed"}}}]}
    rows = await execute_query(db, query)
    if not rows or not rows[0].get("scheduled"):
        return FallbackResult(None, params, t("no_data", lang), [], None, [query])
    r = rows[0]
    pct = r["performed"] / r["scheduled"] * 100
    text = t("fb_surgeries", lang, scheduled=r["scheduled"], performed=r["performed"], pct=fmt_number(pct, lang))
    clean = [{"scheduled": r["scheduled"], "performed": r["performed"], "completion_pct": round(pct, 1)}]
    return FallbackResult(None, params, text, clean, None, [query])


async def _wait_by_triage(db, params, lang, start, end, reference) -> FallbackResult:
    query = {"collection": "admissions", "operation": "aggregate", "pipeline": [
        {"$match": {"admission_route": "Urgencias", "wait_minutes": {"$ne": None}, "triage.level": {"$ne": None},
                    "admission_date": {"$gte": start, "$lte": end}}},
        {"$group": {"_id": "$triage.level", "avg_wait_minutes": {"$avg": "$wait_minutes"}, "patients": {"$sum": 1}}},
        {"$sort": {"_id": 1}}]}
    rows = [{"triage_level": r["_id"], "avg_wait_minutes": round(r["avg_wait_minutes"], 1), "patients": r["patients"]}
            for r in await execute_query(db, query)]
    if not rows:
        return FallbackResult(None, params, t("no_data", lang), [], None, [query])
    lines = "\n".join(f"- Triage {r['triage_level']}: {fmt_number(r['avg_wait_minutes'], lang)} min ({r['patients']})" for r in rows)
    return FallbackResult(None, params, t("fb_wait_by_triage", lang, lines=lines), rows,
                          {"type": "bar", "x": "triage_level", "y": "avg_wait_minutes"}, [query])


async def _specialty_demand(db, params, lang, start, end, reference) -> FallbackResult:
    query = {"collection": "service_demand_daily", "operation": "aggregate", "pipeline": [
        {"$match": {"date": {"$gte": start, "$lte": end}}},
        {"$group": {"_id": "$specialty", "services": {"$sum": "$lines"}}},
        {"$sort": {"services": -1}}, {"$limit": int(params.get("top") or 10)}]}
    rows = [{"specialty": r["_id"], "services": r["services"]} for r in await execute_query(db, query)]
    if not rows:
        return FallbackResult(None, params, t("no_data", lang), [], None, [query])
    text = t("fb_specialty_demand", lang, period=_period_label(start, end, lang), lines=_lines(rows, "specialty", "services", lang))
    return FallbackResult(None, params, text, rows, {"type": "bar", "x": "specialty", "y": "services"}, [query])


HANDLERS = {
    "occupancy": _occupancy,
    "low_inventory": _low_inventory,
    "er_wait": _er_wait,
    "top_service": _top_service,
    "top_meds": _top_meds,
    "least_meds": _least_meds,
    "surgeries": _surgeries,
    "wait_by_triage": _wait_by_triage,
    "specialty_demand": _specialty_demand,
}
