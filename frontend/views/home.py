"""Home: alert ticker, data status, headline indicators and the demo questions."""
import streamlit as st

from core import api_client
from core.i18n import fmt_num, lang, t
from core.icons import page_header
from core.loader import ambulance_loader
from core.ui import alert_bar, api_call, metric, minutes

page_header(t("nav_home"), "home", t("home_intro"))
token = st.session_state["token"]

status = api_call(api_client.cached_get, "/data/status", token, lang=lang())
if not status:
    st.stop()
if not status.get("loaded"):
    st.info(t("no_data_loaded"))
    st.stop()
alert_bar()
st.caption(f"{t('data_until')}: {status['reference_date'][:16].replace('T', ' ')}")

with ambulance_loader(t("loading_kpis")):
    kpis = api_call(api_client.cached_get, "/kpis", token, lang=lang())
if kpis:
    cards = kpis["cards"]
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        metric(t("card_occupancy"), f"{fmt_num(cards['occupancy_pct'])}%",
               t("beds_occupied", occ=cards["occupied_beds"], beds=cards["total_beds"]))
    with c2:
        metric(t("card_er_wait"), minutes(cards["er_wait_mean_minutes"]),
               t("median_minutes", m=fmt_num(cards["er_wait_median_minutes"])))
    with c3:
        metric(t("card_low_stock"), cards["low_stock_items"],
               t("under_days", d=fmt_num(kpis["low_stock_threshold_days"], 0)))
    with c4:
        metric(t("card_surgery"), f"{fmt_num(cards['surgery_completion_pct'])}%", t("of_scheduled"))

st.subheader(t("try_asking"))
columns = st.columns(2, gap="medium")
for i, key in enumerate(("q1", "q2", "q3", "q4")):
    if columns[i % 2].button(f":material/chat: {t(key)}", key=f"home_{key}", use_container_width=True):
        st.session_state["pending_question"] = t(key)
        st.switch_page("views/agent.py")
