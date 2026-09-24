"""Active alerts; administrators can force an evaluation that notifies n8n/Telegram."""
import streamlit as st

from core import api_client
from core.i18n import lang, t
from core.icons import page_header
from core.ui import alert_card, api_call

page_header(t("nav_alerts"), "alert", t("alerts_intro"))

if api_client.is_admin() and st.button(t("evaluate_now"), type="primary", icon=":material/send:"):
    result = api_call(api_client.post, "/alerts/evaluate")
    if result:
        st.success(t("evaluated", **result))
        st.cache_data.clear()

alerts = api_call(api_client.get, "/alerts", lang=lang()) or []
if not alerts:
    st.info(t("no_alerts"))
for severity in ("critical", "high", "medium"):
    group = [a for a in alerts if a["severity"] == severity]
    if group:
        st.subheader(f"{t('severity_' + severity)} ({len(group)})")
        for alert in group:
            alert_card(alert)
