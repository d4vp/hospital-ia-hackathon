"""Dashboard: alert ticker, filters, KPI cards and six analysis tabs."""
from datetime import date

import pandas as pd
import streamlit as st

from core import api_client
from core.i18n import fmt_num, lang, t
from core.icons import page_header
from core.loader import ambulance_loader
from core.ui import alert_bar, api_call, bar, line, metric, minutes, show_table

page_header(t("nav_dashboard"), "bar-chart")
token = st.session_state["token"]
alert_bar()

options = api_call(api_client.cached_get, "/filters", token, lang=lang())
if not options:
    st.stop()

# ---------- Filters ----------
with st.expander(t("filters"), expanded=True):
    f1, f2, f3 = st.columns([2, 1, 1])
    dmin, dmax = date.fromisoformat(options["date_min"]), date.fromisoformat(options["date_max"])
    period = f1.date_input(t("date_range"), value=(dmin, dmax), min_value=dmin, max_value=dmax, format="DD/MM/YYYY", key="dash_date_range")
    bed_group = f2.selectbox(t("bed_group"), [None] + options["bed_groups"], format_func=lambda v: v or t("all"), key="dash_bed_group")
    specialty = f3.selectbox(t("specialty"), [None] + options["specialties"], format_func=lambda v: v or t("all"), key="dash_specialty")
start, end = (period if isinstance(period, tuple) and len(period) == 2 else (dmin, dmax))

params = {"start": start.isoformat(), "end": end.isoformat(), "bed_group": bed_group, "specialty": specialty, "lang": lang()}
with ambulance_loader(t("loading_kpis")):
    data = api_call(api_client.cached_get, "/kpis", token, **params)
if not data:
    st.stop()
cards = data["cards"]

# ---------- KPI cards ----------
c1, c2, c3, c4, c5 = st.columns(5)
with c1:
    metric(t("card_admissions"), fmt_num(cards["admissions"], 0))
with c2:
    metric(t("card_occupancy"), f"{fmt_num(cards['occupancy_pct'])}%",
           t("beds_occupied", occ=cards["occupied_beds"], beds=cards["total_beds"]))
with c3:
    metric(t("card_er_wait"), minutes(cards["er_wait_mean_minutes"]),
           t("median_minutes", m=fmt_num(cards["er_wait_median_minutes"])))
with c4:
    metric(t("card_low_stock"), cards["low_stock_items"], t("under_days", d=fmt_num(data["low_stock_threshold_days"], 0)))
with c5:
    metric(t("card_surgery"), f"{fmt_num(cards['surgery_completion_pct'])}%" if cards["surgery_completion_pct"] is not None else "—",
           t("of_scheduled"))

# ---------- Tabs ----------
tabs = st.tabs([t("tab_occupancy"), t("tab_waits"), t("tab_surgery"), t("tab_demand"), t("tab_meds"), t("tab_patients")])

with tabs[0]:
    occ = pd.DataFrame(data["occupancy_now"])
    bar(occ, "bed_group", "occupancy_pct", t("chart_occupancy_now"), horizontal=True)
    trend = pd.DataFrame(data["occupancy_trend"]["daily"])
    if not trend.empty:
        trend["date"] = pd.to_datetime(trend["date"])
        line(trend, "date", "occupancy_pct", t("chart_occupancy_trend"), threshold=85)
    by_group = pd.DataFrame(data["occupancy_trend"]["by_group"])
    if not by_group.empty and bed_group is None:
        by_group["date"] = pd.to_datetime(by_group["date"])
        line(by_group, "date", "census", t("chart_census_by_group"), color="bed_group")
    st.caption(t("census_note"))
    bar(pd.DataFrame(data["admissions_by_group"]), "bed_group", "admissions", t("chart_admissions_group"), horizontal=True)

with tabs[1]:
    w1, w2 = st.columns(2)
    with w1:
        bar(pd.DataFrame(data["wait"]["by_triage"]), "triage_level", "mean_minutes", t("chart_wait_triage"))
    with w2:
        bar(pd.DataFrame(data["wait"]["by_shift"]), "shift", "mean_minutes", t("chart_wait_shift"))
    show_table(pd.DataFrame(data["wait"]["by_triage"]))

with tabs[2]:
    surgery = data["surgeries"]
    s1, s2, s3 = st.columns(3)
    with s1:
        metric(t("surgeries_scheduled"), surgery["scheduled"])
    with s2:
        metric(t("surgeries_performed"), surgery["performed"])
    with s3:
        metric(t("completion"), f"{fmt_num(surgery['completion_pct'])}%" if surgery["completion_pct"] is not None else "—")
    monthly = pd.DataFrame(surgery["monthly"])
    if not monthly.empty:
        long = monthly.melt(id_vars="month", value_vars=["surgeries_scheduled", "surgeries_performed"],
                            var_name="kind", value_name="quantity")
        long["kind"] = long["kind"].map({"surgeries_scheduled": t("surgeries_scheduled"),
                                         "surgeries_performed": t("surgeries_performed")})
        bar(long, "month", "quantity", t("chart_surgery"), color="kind")
    st.caption(t("surgery_note"))

with tabs[3]:
    d1, d2 = st.columns(2)
    with d1:
        bar(pd.DataFrame(data["demand_by_specialty"]), "specialty", "services", t("chart_specialty"), horizontal=True)
    with d2:
        bar(pd.DataFrame(data["demand_by_area"]), "area", "services", t("chart_area"), horizontal=True)

with tabs[4]:
    m1, m2 = st.columns(2)
    with m1:
        bar(pd.DataFrame(data["top_medications"]), "medication", "quantity", t("chart_top_meds"), horizontal=True)
    with m2:
        bar(pd.DataFrame(data["lowest_rotation_medications"]), "medication", "quantity", t("chart_low_meds"), horizontal=True)
    st.subheader(t("low_stock_title"))
    st.caption(t("synthetic_note"))
    show_table(pd.DataFrame(data["low_stock"]))

with tabs[5]:
    st.caption(t("patients_note"))
    page = st.number_input(t("page"), min_value=1, value=1, step=1, key="patients_page")
    patients = api_call(api_client.get, "/patients", page=int(page), size=50, **{k: v for k, v in params.items() if k != "lang"})
    if patients:
        st.caption(t("rows_total", n=fmt_num(patients["total"], 0)))
        show_table(pd.DataFrame(patients["rows"]))
