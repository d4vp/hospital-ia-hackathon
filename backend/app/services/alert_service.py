"""Rule-based alerts, their attention workflow and the n8n webhook (n8n forwards to Telegram).

Flow
1. `compute_alerts(frames, lang)`  pure rules over the analytical frames (memoized per ETL run
                                   and language by `cached_alerts`).
2. `evaluate_and_notify(db)`       persists alerts in `alerts`; alerts that were not active
                                   before are "new" and are POSTed to N8N_WEBHOOK_URL
                                   (concurrently, one shared HTTP client); alerts that
                                   disappeared are marked as resolved and archived.
3. A background task runs step 2 every ALERT_CHECK_INTERVAL_SECONDS and after each ETL.

Attention workflow (per alert):  new -> reviewed -> in_progress -> finalized
- Every transition is recorded (who, when, optional note) and sent to n8n.
- Finalized alerts leave the active view and are copied to `alert_history`; while the
  condition persists they stay silenced, unless the severity escalates (then they reopen).
- Alerts whose condition disappears are archived as "resolved".

The backend never talks to Telegram directly: it only fires the webhook.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import httpx
import numpy as np
import pandas as pd

from app.core.config import COLLECTIONS, settings
from app.core.i18n import fmt_number, t
from app.services import analytics as an
from app.services import cache
from app.services.analytics import Frames
from app.services.data_repository import DatasetNotLoadedError, get_frames

logger = logging.getLogger("alerts")

# Where stable patients can be moved when a unit is saturated (step-down care).
STEP_DOWN = {
    "UNIDAD DE CUIDADO INTENSIVO": "UNIDAD DE CUIDADO INTERMEDIO",
    "UNIDAD DE CUIDADO INTERMEDIO": "HOSPITALIZACION",
    "URGENCIAS": "HOSPITALIZACION",
    "RECUPERACION": "HOSPITALIZACION",
    "SALA PARTOS": "GINECO OBSTRETICIA",
    "UNIDAD DE CUIDADO BASICO": "PEDIATRIA",
}
MAX_INVENTORY_ALERTS = 10
MIN_DAILY_USE_FOR_ALERT = 1.0  # ignore very low-rotation items to avoid alert fatigue
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2}


def _alert(key: str, kind: str, severity: str, subject: str, message: str,
           recommendation: str, data: dict[str, Any]) -> dict:
    return {
        "key": key, "type": kind, "severity": severity, "subject": subject,
        "message": message, "recommendation": recommendation, "data": data,
    }


def compute_alerts(frames: Frames, lang: str = "es") -> list[dict]:
    alerts: list[dict] = []
    ref = frames.reference_date
    adm = frames.admissions

    # 1) Bed occupancy by group.
    occupancy = an.current_occupancy(frames)
    by_group = {row["bed_group"]: row for row in occupancy}
    threshold = settings.ALERT_OCCUPANCY_THRESHOLD_PCT
    for row in occupancy:
        pct = row["occupancy_pct"]
        if pct is None or pct <= threshold:
            continue
        severity = "critical" if pct >= 100 else "high" if pct >= 95 else "medium"
        target = STEP_DOWN.get(row["bed_group"])
        target_row = by_group.get(target) if target else None
        if target_row and (target_row["occupancy_pct"] or 0) < threshold:
            action = t("alert_occupancy_action", lang, target=target, target_pct=fmt_number(target_row["occupancy_pct"], lang))
        else:
            action = t("alert_occupancy_action_generic", lang)
        alerts.append(_alert(
            f"occupancy:{row['bed_group']}", "occupancy", severity, row["bed_group"],
            t("alert_occupancy", lang, group=row["bed_group"], pct=fmt_number(pct, lang),
              occupied=row["occupied"], beds=row["beds"], threshold=fmt_number(threshold, lang, 0)),
            action, row,
        ))

    # 2) Medication stock-outs (synthetic stock, real consumption).
    inv_threshold = settings.ALERT_INVENTORY_DAYS_THRESHOLD
    low = [r for r in an.low_stock(frames, inv_threshold) if (r["avg_daily_consumption"] or 0) >= MIN_DAILY_USE_FOR_ALERT]
    for row in sorted(low, key=lambda r: r["days_of_inventory"])[:MAX_INVENTORY_ALERTS]:
        severity = "critical" if row["days_of_inventory"] < 2 else "high"
        order_qty = int(np.ceil(row["avg_daily_consumption"] * 15 - row["stock"]))
        alerts.append(_alert(
            f"inventory:{row['code']}", "inventory", severity, row["name"],
            t("alert_inventory", lang, name=row["name"], days=fmt_number(row["days_of_inventory"], lang),
              stock=row["stock"], daily=fmt_number(row["avg_daily_consumption"], lang)),
            t("alert_inventory_action", lang, qty=max(order_qty, 1)), {**row, "synthetic": True},
        ))

    # 3) ER waiting time (last 7 days) + worst shift.
    last_week = adm[adm["admission_date"] >= ref - pd.Timedelta(days=7)]
    waits = an.er_waits(last_week)
    er_threshold = settings.ALERT_ER_WAIT_THRESHOLD_MIN
    if len(waits) >= 20 and waits.mean() > er_threshold:
        shifts = an.wait_by_shift(last_week)
        worst = max(shifts, key=lambda s: s["mean_minutes"] or 0) if shifts else None
        severity = "high" if waits.mean() > er_threshold * 1.5 else "medium"
        alerts.append(_alert(
            "er_wait:7d", "er_wait", severity, "Urgencias",
            t("alert_er_wait", lang, mean=fmt_number(waits.mean(), lang), threshold=fmt_number(er_threshold, lang, 0)),
            t("alert_er_wait_action", lang, shift=t(f"shift_{worst['shift']}", lang),
              shift_mean=fmt_number(worst["mean_minutes"], lang)) if worst else "",
            {"mean_minutes": round(float(waits.mean()), 1), "patients": int(len(waits)), "by_shift": shifts},
        ))

    # 4) Triage 2 target (Resolución 5596 de 2015: up to 30 minutes).
    triage2 = last_week[(last_week["admission_route"] == an.ER_ROUTE) & (last_week["triage_level"] == 2)]["wait_minutes"].dropna()
    t2_threshold = settings.ALERT_TRIAGE2_WAIT_THRESHOLD_MIN
    if len(triage2) >= 5 and triage2.mean() > t2_threshold:
        alerts.append(_alert(
            "triage2_wait:7d", "triage_wait", "high", "Triage 2",
            t("alert_triage2", lang, mean=fmt_number(triage2.mean(), lang), threshold=fmt_number(t2_threshold, lang, 0)),
            t("alert_triage2_action", lang),
            {"mean_minutes": round(float(triage2.mean()), 1), "patients": int(len(triage2))},
        ))

    # 5) Surgery completion (last 30 days).
    last_month = adm[adm["admission_date"] >= ref - pd.Timedelta(days=30)]
    surgery = an.surgery_summary(last_month)
    s_threshold = settings.ALERT_SURGERY_COMPLETION_THRESHOLD_PCT
    if surgery["scheduled"] >= 10 and surgery["completion_pct"] is not None and surgery["completion_pct"] < s_threshold:
        alerts.append(_alert(
            "surgery_completion:30d", "surgery", "medium", "Quirófanos",
            t("alert_surgery", lang, pct=fmt_number(surgery["completion_pct"], lang),
              performed=surgery["performed"], scheduled=surgery["scheduled"]),
            t("alert_surgery_action", lang),
            {k: surgery[k] for k in ("scheduled", "performed", "completion_pct")},
        ))

    return sorted(alerts, key=lambda a: SEVERITY_ORDER.get(a["severity"], 9))




# --------------------------------------------------------------------------- #
# Memoized rules + attention workflow
# --------------------------------------------------------------------------- #
LANGS = ("es", "en")
_alerts_cache = cache.register(maxsize=8, ttl=settings.CACHE_TTL_SECONDS)


async def cached_alerts(frames: Frames, lang: str) -> list[dict]:
    """compute_alerts memoized per ETL run and language. Callers must not mutate the result."""
    return await cache.memoize(_alerts_cache, (frames.run_id, lang),
                               lambda: asyncio.to_thread(compute_alerts, frames, lang))


class WorkflowStatus(str, Enum):
    NEW = "new"
    REVIEWED = "reviewed"
    IN_PROGRESS = "in_progress"
    FINALIZED = "finalized"


# Forward-only state machine: an attended alert never silently goes back to "new".
ALLOWED_TRANSITIONS: dict[WorkflowStatus, frozenset[WorkflowStatus]] = {
    WorkflowStatus.NEW: frozenset({WorkflowStatus.REVIEWED, WorkflowStatus.IN_PROGRESS, WorkflowStatus.FINALIZED}),
    WorkflowStatus.REVIEWED: frozenset({WorkflowStatus.IN_PROGRESS, WorkflowStatus.FINALIZED}),
    WorkflowStatus.IN_PROGRESS: frozenset({WorkflowStatus.FINALIZED}),
    WorkflowStatus.FINALIZED: frozenset(),
}
CLOSED_FINALIZED = "finalized"
CLOSED_RESOLVED = "resolved"


class AlertNotFoundError(LookupError):
    """The alert is not active (never existed, resolved or already archived)."""


class InvalidTransitionError(ValueError):
    """The requested status change is not allowed from the current status."""


def _workflow_status(doc: dict) -> WorkflowStatus:
    try:
        return WorkflowStatus(doc.get("workflow_status") or WorkflowStatus.NEW.value)
    except ValueError:
        return WorkflowStatus.NEW


def _escalated(stored: dict, current: dict) -> bool:
    """True when the alert became more severe than when it was finalized."""
    return SEVERITY_ORDER.get(current["severity"], 9) < SEVERITY_ORDER.get(stored.get("severity"), 9)


def headline(alert: dict, lang: str) -> str:
    """Very short figure for the alert ticker (e.g. '102,5%', '1,5 d', '75,0 min')."""
    data = alert.get("data") or {}
    match alert.get("type"):
        case "occupancy":
            return f"{fmt_number(data.get('occupancy_pct'), lang)}%"
        case "inventory":
            return f"{fmt_number(data.get('days_of_inventory'), lang)} d"
        case "er_wait" | "triage_wait":
            return f"{fmt_number(data.get('mean_minutes'), lang)} min"
        case "surgery":
            return f"{fmt_number(data.get('completion_pct'), lang)}%"
        case _:
            return ""


# --------------------------------------------------------------------------- #
# n8n webhook
# --------------------------------------------------------------------------- #
_http: Optional[httpx.AsyncClient] = None


def _http_client() -> httpx.AsyncClient:
    """One pooled client for every webhook call (no TLS handshake per alert)."""
    global _http
    if _http is None or _http.is_closed:
        _http = httpx.AsyncClient(timeout=10.0)
    return _http


async def close_http_client() -> None:
    global _http
    if _http is not None:
        await _http.aclose()
        _http = None


def telegram_text(alert: dict, lang: str) -> str:
    severity = t(f"severity_{alert['severity']}", lang)
    return (
        f"[{severity}] Hospital Susana López de Valencia\n"
        f"{alert['message']}\n{alert['recommendation']}".strip()
    )


def _alert_block(alert: dict, lang: str) -> dict:
    detected = alert.get("first_seen") or datetime.now(timezone.utc)
    return {
        "key": alert["key"], "type": alert["type"], "severity": alert["severity"],
        "severity_label": t(f"severity_{alert['severity']}", lang),
        "subject": alert["subject"], "message": alert["message"],
        "recommendation": alert["recommendation"], "data": alert.get("data", {}),
        "detected_at": detected.isoformat() if isinstance(detected, datetime) else str(detected),
        "workflow_status": alert.get("workflow_status", WorkflowStatus.NEW.value),
    }


async def _post_to_n8n(payload: dict, key: str) -> Optional[str]:
    """POSTs a payload to the n8n webhook. Returns an error string or None on success."""
    if not settings.N8N_WEBHOOK_URL:
        return "N8N_WEBHOOK_URL not configured"
    headers = {"X-Webhook-Secret": settings.N8N_WEBHOOK_SECRET} if settings.N8N_WEBHOOK_SECRET else {}
    try:
        response = await _http_client().post(settings.N8N_WEBHOOK_URL, json=payload, headers=headers)
        response.raise_for_status()
        return None
    except httpx.HTTPError as exc:
        logger.warning("n8n_webhook_failed", extra={"data": {"key": key, "event": payload.get("event"), "error": str(exc)}})
        return str(exc)


async def send_to_n8n(alert: dict, lang: str) -> Optional[str]:
    """New (or re-opened) alert -> n8n event `hospital_alert`."""
    payload = {
        "event": "hospital_alert",
        "hospital": "Hospital Susana López de Valencia",
        "language": lang,
        "alert": _alert_block(alert, lang),
        "telegram_text": telegram_text(alert, lang),
    }
    return await _post_to_n8n(payload, alert["key"])


async def notify_status_change(alert: dict, status: WorkflowStatus, actor: str, note: str, lang: str) -> Optional[str]:
    """Workflow transition -> n8n event `hospital_alert_status` (e.g. to update the Telegram thread)."""
    status_label = t(f"workflow_{status.value}", lang)
    text = t("alert_status_changed", lang, subject=alert["subject"], status=status_label, actor=actor)
    if note:
        text += f"\n{note}"
    payload = {
        "event": "hospital_alert_status",
        "hospital": "Hospital Susana López de Valencia",
        "language": lang,
        "alert": {**_alert_block(alert, lang), "workflow_status": status.value},
        "status": {"value": status.value, "label": status_label, "by": actor, "note": note,
                   "at": datetime.now(timezone.utc).isoformat()},
        "telegram_text": text,
    }
    return await _post_to_n8n(payload, alert["key"])


# --------------------------------------------------------------------------- #
# Persistence + notification
# --------------------------------------------------------------------------- #
def _jsonable(value: Any) -> Any:
    """Converts numpy scalars AND returns a deep copy, so cached rule results stay untouched."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    return value


async def _texts_by_key(frames: Frames) -> dict[str, dict[str, dict[str, str]]]:
    """{alert_key: {lang: {message, recommendation}}} so archived alerts stay bilingual."""
    per_lang = await asyncio.gather(*(cached_alerts(frames, lang) for lang in LANGS))
    texts: dict[str, dict[str, dict[str, str]]] = {}
    for lang, alerts in zip(LANGS, per_lang):
        for alert in alerts:
            texts.setdefault(alert["key"], {})[lang] = {
                "message": alert["message"], "recommendation": alert["recommendation"]}
    return texts


def _history_entry(doc: dict, reason: str, now: datetime, actor: Optional[str] = None) -> dict:
    return {
        "alert_key": doc["_id"], "type": doc.get("type"), "severity": doc.get("severity"),
        "subject": doc.get("subject"), "message": doc.get("message"), "recommendation": doc.get("recommendation"),
        "i18n": doc.get("i18n") or {}, "data": doc.get("data") or {},
        "first_seen": doc.get("first_seen"), "last_seen": doc.get("last_seen"),
        "closed_at": now, "closed_reason": reason, "closed_by": actor,
        "workflow_status": doc.get("workflow_status", WorkflowStatus.NEW.value),
        "workflow_log": doc.get("workflow_log") or [],
    }


def _new_incident(alert: dict, now: datetime, error: Optional[str]) -> dict:
    return {
        **alert, "_id": alert["key"], "status": "active", "first_seen": now, "last_seen": now,
        "notified_at": None if error else now, "notify_error": error,
        "workflow_status": WorkflowStatus.NEW.value, "workflow_log": [],
        "workflow_updated_at": None, "workflow_updated_by": None, "workflow_note": None,
    }


async def evaluate_and_notify(db) -> dict:
    lang = settings.ALERT_LANGUAGE
    try:
        frames = await get_frames(db)
    except DatasetNotLoadedError:
        return {"active": 0, "new": 0, "resolved": 0, "reopened": 0}
    now = datetime.now(timezone.utc)
    collection = db[COLLECTIONS["alerts"]]
    current = [_jsonable(a) for a in await cached_alerts(frames, lang)]
    texts = await _texts_by_key(frames)
    current_keys = {a["key"] for a in current}
    previous = {doc["_id"]: doc async for doc in collection.find({"status": "active"})}

    to_notify: list[dict] = []
    refreshes = []
    reopened = 0
    for alert in current:
        alert["i18n"] = texts.get(alert["key"], {})
        stored = previous.get(alert["key"])
        if stored is None:
            to_notify.append(alert)
        elif _workflow_status(stored) is WorkflowStatus.FINALIZED and _escalated(stored, alert):
            to_notify.append(alert)
            reopened += 1
        else:  # still active: refresh the figures, keep the workflow fields untouched
            refreshes.append(collection.update_one({"_id": alert["key"]}, {"$set": {**alert, "last_seen": now}}))

    for alert in to_notify:
        alert["first_seen"] = now
    errors = await asyncio.gather(*(send_to_n8n(alert, lang) for alert in to_notify))
    writes = [
        collection.replace_one({"_id": alert["key"]}, _new_incident(alert, now, error), upsert=True)
        for alert, error in zip(to_notify, errors)
    ]
    await asyncio.gather(*refreshes, *writes)

    resolved = [k for k in previous if k not in current_keys]
    if resolved:
        # Finalized alerts were archived when they were closed; archive the rest now.
        archive = [_history_entry(previous[k], CLOSED_RESOLVED, now) for k in resolved
                   if _workflow_status(previous[k]) is not WorkflowStatus.FINALIZED]
        if archive:
            await db[COLLECTIONS["alert_history"]].insert_many(archive)
        await collection.update_many(
            {"_id": {"$in": resolved}}, {"$set": {"status": "resolved", "resolved_at": now}}
        )
    summary = {"active": len(current), "new": len(to_notify) - reopened, "resolved": len(resolved), "reopened": reopened}
    logger.info("alerts_evaluated", extra={"data": summary})
    return summary


_WORKFLOW_PROJECTION = {"first_seen": 1, "notified_at": 1, "workflow_status": 1, "workflow_updated_at": 1,
                        "workflow_updated_by": 1, "workflow_note": 1}


async def active_alerts(db, lang: str) -> list[dict]:
    """Current alerts in the requested language, with their workflow status.

    Finalized alerts are left out: they live in the history until the condition clears.
    """
    frames = await get_frames(db)
    alerts = [_jsonable(a) for a in await cached_alerts(frames, lang)]
    stored = {doc["_id"]: doc async for doc in db[COLLECTIONS["alerts"]].find({"status": "active"}, _WORKFLOW_PROJECTION)}
    visible: list[dict] = []
    for alert in alerts:
        doc = stored.get(alert["key"], {})
        status = _workflow_status(doc)
        if status is WorkflowStatus.FINALIZED:
            continue
        alert.update(
            first_seen=doc.get("first_seen"), notified=bool(doc.get("notified_at")), workflow_status=status.value,
            workflow_updated_at=doc.get("workflow_updated_at"), workflow_updated_by=doc.get("workflow_updated_by"),
            workflow_note=doc.get("workflow_note"), headline=headline(alert, lang),
            next_statuses=[s.value for s in WorkflowStatus if s in ALLOWED_TRANSITIONS[status]],
        )
        visible.append(alert)
    return visible


async def alerts_summary(db, lang: str) -> dict:
    """Compact payload for the alert ticker: counts plus one short line per alert."""
    alerts = await active_alerts(db, lang)
    by_severity = {severity: 0 for severity in SEVERITY_ORDER}
    by_status = {status.value: 0 for status in WorkflowStatus if status is not WorkflowStatus.FINALIZED}
    for alert in alerts:
        by_severity[alert["severity"]] = by_severity.get(alert["severity"], 0) + 1
        by_status[alert["workflow_status"]] = by_status.get(alert["workflow_status"], 0) + 1
    items = [{k: alert.get(k) for k in ("key", "type", "severity", "subject", "headline", "workflow_status")}
             for alert in alerts]
    return {"total": len(alerts), "by_severity": by_severity, "by_status": by_status, "items": items}


async def _ensure_stored(db, key: str) -> Optional[dict]:
    """An alert computed live but not persisted yet (scheduler disabled / not run yet)."""
    frames = await get_frames(db)
    lang = settings.ALERT_LANGUAGE
    live = next((a for a in await cached_alerts(frames, lang) if a["key"] == key), None)
    if live is None:
        return None
    alert = _jsonable(live)
    alert["i18n"] = (await _texts_by_key(frames)).get(key, {})
    doc = _new_incident(alert, datetime.now(timezone.utc), "not notified: created by a status change")
    await db[COLLECTIONS["alerts"]].replace_one({"_id": key}, doc, upsert=True)
    return doc


async def change_status(db, key: str, new_status: WorkflowStatus, actor: str, note: str = "") -> dict:
    """Applies one workflow transition atomically. Returns the updated alert document."""
    collection = db[COLLECTIONS["alerts"]]
    doc = await collection.find_one({"_id": key, "status": "active"}) or await _ensure_stored(db, key)
    if doc is None:
        raise AlertNotFoundError(f"Alert not active: {key}")
    current = _workflow_status(doc)
    if new_status not in ALLOWED_TRANSITIONS[current]:
        raise InvalidTransitionError(f"Cannot change an alert from '{current.value}' to '{new_status.value}'")

    now = datetime.now(timezone.utc)
    note = (note or "").strip()[:500]
    entry = {"status": new_status.value, "by": actor, "at": now, "note": note}
    # The filter on the previous status makes the transition atomic (no lost concurrent update).
    result = await collection.update_one(
        {"_id": key, "status": "active", "workflow_status": doc.get("workflow_status")},
        {"$set": {"workflow_status": new_status.value, "workflow_updated_at": now,
                  "workflow_updated_by": actor, "workflow_note": note or None},
         "$push": {"workflow_log": entry}},
    )
    if result.modified_count == 0:
        raise InvalidTransitionError("The alert was updated by someone else; reload and try again")
    doc.update(workflow_status=new_status.value, workflow_updated_at=now, workflow_updated_by=actor,
               workflow_note=note or None, workflow_log=[*(doc.get("workflow_log") or []), entry])
    if new_status is WorkflowStatus.FINALIZED:
        await db[COLLECTIONS["alert_history"]].insert_one(_history_entry(doc, CLOSED_FINALIZED, now, actor))
    logger.info("alert_status_changed", extra={"data": {"key": key, "from": current.value,
                                                         "to": new_status.value, "by": actor}})
    return doc


def public_alert(doc: dict, lang: str) -> dict:
    """Alert document as returned by the API (localized texts, no internal fields)."""
    texts = (doc.get("i18n") or {}).get(lang) or {}
    out = {k: v for k, v in doc.items() if k not in ("_id", "i18n")}
    out["key"] = doc.get("key") or doc.get("alert_key") or doc.get("_id")
    out["message"] = texts.get("message", doc.get("message"))
    out["recommendation"] = texts.get("recommendation", doc.get("recommendation"))
    return _jsonable(out)


async def alert_history(db, lang: str, limit: int = 100, severity: Optional[str] = None,
                        reason: Optional[str] = None, alert_type: Optional[str] = None) -> list[dict]:
    """Closed alerts (finalized by staff or resolved by the data), newest first."""
    query: dict[str, Any] = {}
    for field, value in (("severity", severity), ("closed_reason", reason), ("type", alert_type)):
        if value:
            query[field] = value
    cursor = db[COLLECTIONS["alert_history"]].find(query, {"data": 0}).sort("closed_at", -1).limit(limit)
    return [public_alert(doc, lang) async for doc in cursor]
