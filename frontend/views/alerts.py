"""Alerts: active alerts with their attention workflow, closed alerts and the status history.

Workflow: new -> reviewed -> in progress -> finalized.
- "Atender" moves an alert to "En progreso": it stays in the active view (never deleted),
  clearly marked, and stops counting as pending, so the dashboard counters go down.
- Every change is kept in the alert's own history and in the global status history.
- Finalized alerts move to "Alertas cerradas", together with alerts the system resolved.
Administrators can also force an evaluation that notifies n8n/Telegram.
"""
from typing import Any, Optional
from urllib.parse import quote

import pandas as pd
import streamlit as st

from core import api_client
from core.i18n import lang, t, value_label
from core.icons import page_header
from core.ui import SEVERITIES, alert_card, alert_ticker, api_call, show_table

WORKFLOW = ("new", "reviewed", "in_progress")  # statuses visible in the active view
PENDING = frozenset({"new", "reviewed"})
HISTORY_COLUMNS = ["closed_at", "severity", "subject", "message", "closed_reason", "closed_by", "first_seen"]
EVENT_COLUMNS = ["at", "severity", "subject", "from_status", "to_status", "by", "note"]

page_header(t("nav_alerts"), "alert", t("alerts_intro"))
token = st.session_state["token"]

if flash := st.session_state.pop("alerts_flash", None):
    st.success(flash)

if api_client.is_admin() and st.button(f":material/send: {t('evaluate_now')}", type="primary"):
    result = api_call(api_client.post, "/alerts/evaluate")
    if result:
        st.success(t("evaluated", **result))
        api_client.live_get.clear()


def _ts(value: Optional[Any]) -> str:
    return str(value or "")[:16].replace("T", " ")


def _refresh(message: str) -> None:
    st.session_state["alerts_flash"] = message
    api_client.live_get.clear()
    st.rerun()


def _change_status(alert: dict, new_status: str, note: str = "") -> None:
    path = f"/alerts/{quote(alert['key'], safe='')}"
    if api_call(api_client.patch, path, {"status": new_status, "note": note}):
        message = t("status_saved", status=t("workflow_" + new_status))
        extra = {"finalized": t("finalized_moved"), "in_progress": t("attended_ok")}.get(new_status, "")
        _refresh(f"{message} {extra}".strip())


def status_form(alert: dict, index: int) -> None:
    options = alert.get("next_statuses") or []
    if not options:
        return
    with st.form(f"alert_status_{index}", border=False):
        c1, c2, c3 = st.columns([2, 3, 1.4], vertical_alignment="bottom")
        new_status = c1.selectbox(t("next_status"), options, format_func=lambda s: t("workflow_" + s),
                                  key=f"alert_next_{index}")
        note = c2.text_input(t("status_note"), max_chars=500, key=f"alert_note_{index}")
        if c3.form_submit_button(t("save_status"), use_container_width=True):
            _change_status(alert, new_status, note)


def status_history(log: list[dict]) -> None:
    if not log:
        st.caption("—")
        return
    df = pd.DataFrame(log)
    df["at"] = df["at"].map(_ts)
    show_table(df[[c for c in ("at", "status", "by", "note") if c in df]])


alerts = api_call(api_client.live_get, "/alerts", token, lang=lang()) or []
summary = api_call(api_client.live_get, "/alerts/summary", token, lang=lang())
tab_active, tab_history = st.tabs([t("tab_active_alerts", n=len(alerts)), t("tab_alert_history")])

# ---------- Active ----------
with tab_active:
    alert_ticker(summary)
    if alerts:
        st.subheader(t("status_overview"))
        overview = pd.DataFrame([{
            "severity": a["severity"], "subject": a["subject"], "headline": a.get("headline"),
            "workflow_status": a.get("workflow_status", "new"), "workflow_updated_by": a.get("workflow_updated_by") or "—",
            "workflow_updated_at": _ts(a.get("workflow_updated_at")),
        } for a in alerts])
        show_table(overview)

    selected = st.multiselect(t("workflow_filter"), WORKFLOW, default=list(WORKFLOW),
                              format_func=lambda s: t("workflow_" + s), key="alerts_workflow_filter")
    visible = [a for a in alerts if a.get("workflow_status", "new") in selected]
    if not visible:
        st.info(t("no_alerts"))
    index = 0
    for severity in SEVERITIES:
        group = [a for a in visible if a["severity"] == severity]
        if not group:
            continue
        st.subheader(f"{t('severity_' + severity)} ({len(group)})")
        for alert in group:
            alert_card(alert)
            if alert.get("workflow_status", "new") in PENDING and st.button(
                    f":material/medical_services: {t('attend_now')}", key=f"attend_{index}", type="primary"):
                _change_status(alert, "in_progress")
            with st.expander(t("manage_alert")):
                status_form(alert, index)
            with st.expander(f"{t('status_history')} ({len(alert.get('workflow_log') or [])})"):
                status_history(alert.get("workflow_log") or [])
            index += 1

# ---------- History ----------
with tab_history:
    view = st.radio(t("history_view"), ["closed", "events"], horizontal=True, key="history_view",
                    format_func=lambda v: t("closed_alerts") if v == "closed" else t("status_history"))
    if view == "events":
        st.caption(t("events_intro"))
        events = api_call(api_client.live_get, "/alerts/events", token, limit=500) or []
        if not events:
            st.info(t("no_history"))
        else:
            df = pd.DataFrame(events)
            df["at"] = df["at"].map(_ts)
            show_table(df[[c for c in EVENT_COLUMNS if c in df]])
    else:
        st.caption(t("history_intro"))
        f1, f2 = st.columns(2)
        severity = f1.selectbox(t("severity"), [None, *SEVERITIES], key="history_severity",
                                format_func=lambda v: t("severity_" + v) if v else t("any"))
        reason = f2.selectbox(t("closed_reason"), [None, "finalized", "resolved"], key="history_reason",
                              format_func=lambda v: value_label(v) if v else t("any"))
        history = api_call(api_client.live_get, "/alerts/history", token, lang=lang(),
                           severity=severity, reason=reason, limit=200) or []
        if not history:
            st.info(t("no_history"))
        else:
            df = pd.DataFrame(history)
            for column in ("closed_at", "first_seen"):
                if column in df:
                    df[column] = df[column].map(_ts)
            show_table(df[[c for c in HISTORY_COLUMNS if c in df]])

            st.subheader(t("history_detail"))
            choice = st.selectbox(t("subject"), range(len(history)), key="history_choice",
                                  format_func=lambda i: f"{_ts(history[i]['closed_at'])} · {history[i]['subject']}")
            status_history(history[choice].get("workflow_log") or [])
