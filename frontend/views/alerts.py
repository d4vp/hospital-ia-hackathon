"""Alerts: active alerts with their attention workflow, and the history of closed alerts.

Workflow: new -> reviewed -> in progress -> finalized. Finalized alerts leave the active view
and move to the history, together with alerts the system resolved on its own.
Administrators can also force an evaluation that notifies n8n/Telegram.
"""
from urllib.parse import quote

import pandas as pd
import streamlit as st

from core import api_client
from core.i18n import lang, t, value_label
from core.icons import page_header
from core.ui import SEVERITIES, alert_card, alert_ticker, api_call, show_table

WORKFLOW = ("new", "reviewed", "in_progress")  # statuses visible in the active view
HISTORY_COLUMNS = ["closed_at", "severity", "subject", "message", "closed_reason", "closed_by", "first_seen"]

page_header(t("nav_alerts"), "alert", t("alerts_intro"))
token = st.session_state["token"]

if flash := st.session_state.pop("alerts_flash", None):
    st.success(flash)

if api_client.is_admin() and st.button(f":material/send: {t('evaluate_now')}", type="primary"):
    result = api_call(api_client.post, "/alerts/evaluate")
    if result:
        st.success(t("evaluated", **result))
        api_client.live_get.clear()


def _refresh(message: str) -> None:
    st.session_state["alerts_flash"] = message
    api_client.live_get.clear()
    st.rerun()


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
            path = f"/alerts/{quote(alert['key'], safe='')}"
            if api_call(api_client.patch, path, {"status": new_status, "note": note}):
                message = t("status_saved", status=t("workflow_" + new_status))
                _refresh(f"{message} {t('finalized_moved')}" if new_status == "finalized" else message)


alerts = api_call(api_client.live_get, "/alerts", token, lang=lang()) or []
tab_active, tab_history = st.tabs([t("tab_active_alerts", n=len(alerts)), t("tab_alert_history")])

# ---------- Active ----------
with tab_active:
    counts = {sev: sum(1 for a in alerts if a["severity"] == sev) for sev in SEVERITIES}
    alert_ticker({"by_severity": counts, "items": []} if not alerts else
                 {"by_severity": counts, "items": [{k: a.get(k) for k in ("severity", "subject", "headline",
                                                                           "workflow_status")} for a in alerts]})
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
            with st.expander(t("manage_alert")):
                status_form(alert, index)
            index += 1

# ---------- History ----------
with tab_history:
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
                df[column] = df[column].fillna("").astype(str).str[:16].str.replace("T", " ")
        show_table(df[[c for c in HISTORY_COLUMNS if c in df]])

        st.subheader(t("history_detail"))
        choice = st.selectbox(t("subject"), range(len(history)), key="history_choice",
                              format_func=lambda i: f"{history[i]['closed_at'][:16].replace('T', ' ')} · "
                                                    f"{history[i]['subject']}")
        log = pd.DataFrame(history[choice].get("workflow_log") or [])
        if log.empty:
            st.caption("—")
        else:
            log["at"] = log["at"].astype(str).str[:16].str.replace("T", " ")
            show_table(log[[c for c in ("at", "status", "by", "note") if c in log]])
