"""Rule-based alerts + n8n webhook notification (n8n forwards them to Telegram).

Flow
1. `compute_alerts(frames, lang)`  pure rules over the analytical frames.
2. `evaluate_and_notify(db)`       persists alerts in `alerts`; alerts that were not active
                                   before are "new" and are POSTed to N8N_WEBHOOK_URL;
                                   alerts that disappeared are marked as resolved.
3. A background task runs step 2 every ALERT_CHECK_INTERVAL_SECONDS and after each ETL.

The backend never talks to Telegram directly: it only fires the webhook.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

import httpx
import numpy as np
import pandas as pd

from app.core.config import COLLECTIONS, settings
from app.core.i18n import fmt_number, t
from app.services import analytics as an
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
# n8n webhook
# --------------------------------------------------------------------------- #
def telegram_text(alert: dict, lang: str) -> str:
    severity = t(f"severity_{alert['severity']}", lang)
    return (
        f"[{severity}] Hospital Susana López de Valencia\n"
        f"{alert['message']}\n{alert['recommendation']}".strip()
    )


async def send_to_n8n(alert: dict, lang: str) -> Optional[str]:
    """POSTs the alert to the n8n webhook. Returns an error string or None on success."""
    if not settings.N8N_WEBHOOK_URL:
        return "N8N_WEBHOOK_URL not configured"
    payload = {
        "event": "hospital_alert",
        "hospital": "Hospital Susana López de Valencia",
        "language": lang,
        "alert": {
            "key": alert["key"], "type": alert["type"], "severity": alert["severity"],
            "severity_label": t(f"severity_{alert['severity']}", lang),
            "subject": alert["subject"], "message": alert["message"],
            "recommendation": alert["recommendation"], "data": alert["data"],
            "detected_at": alert.get("first_seen", datetime.now(timezone.utc)).isoformat(),
        },
        "telegram_text": telegram_text(alert, lang),
    }
    headers = {"X-Webhook-Secret": settings.N8N_WEBHOOK_SECRET} if settings.N8N_WEBHOOK_SECRET else {}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(settings.N8N_WEBHOOK_URL, json=payload, headers=headers)
            response.raise_for_status()
        return None
    except httpx.HTTPError as exc:
        logger.warning("n8n_webhook_failed", extra={"data": {"key": alert["key"], "error": str(exc)}})
        return str(exc)


# --------------------------------------------------------------------------- #
# Persistence + notification
# --------------------------------------------------------------------------- #
def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    return value


async def evaluate_and_notify(db) -> dict:
    lang = settings.ALERT_LANGUAGE
    try:
        frames = await get_frames(db)
    except DatasetNotLoadedError:
        return {"active": 0, "new": 0, "resolved": 0}
    now = datetime.now(timezone.utc)
    collection = db[COLLECTIONS["alerts"]]
    current = compute_alerts(frames, lang)
    current_keys = {a["key"] for a in current}
    previous = {doc["_id"]: doc async for doc in collection.find({"status": "active"})}

    new_count = 0
    for alert in current:
        alert = _jsonable(alert)
        if alert["key"] in previous:
            await collection.update_one(
                {"_id": alert["key"]},
                {"$set": {**alert, "last_seen": now}},
            )
            continue
        alert["first_seen"] = now
        error = await send_to_n8n(alert, lang)
        await collection.replace_one(
            {"_id": alert["key"]},
            {**alert, "_id": alert["key"], "status": "active", "first_seen": now, "last_seen": now,
             "notified_at": None if error else now, "notify_error": error},
            upsert=True,
        )
        new_count += 1

    resolved = [k for k in previous if k not in current_keys]
    if resolved:
        await collection.update_many(
            {"_id": {"$in": resolved}}, {"$set": {"status": "resolved", "resolved_at": now}}
        )
    summary = {"active": len(current), "new": new_count, "resolved": len(resolved)}
    logger.info("alerts_evaluated", extra={"data": summary})
    return summary


async def active_alerts(db, lang: str) -> list[dict]:
    """Current alerts rendered in the requested language, with first-seen timestamps."""
    frames = await get_frames(db)
    alerts = compute_alerts(frames, lang)
    stored = {doc["_id"]: doc async for doc in db[COLLECTIONS["alerts"]].find({"status": "active"})}
    for alert in alerts:
        doc = stored.get(alert["key"], {})
        alert["first_seen"] = doc.get("first_seen")
        alert["notified"] = bool(doc.get("notified_at"))
    return _jsonable(alerts)
