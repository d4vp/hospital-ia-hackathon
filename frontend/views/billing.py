"""Optional billing module (admin-only): billable activity by insurer, regime, month and item.

The page is listed only when the backend reports `features.billing = true`, and the API
enforces the administrator role as well.
"""
from datetime import date

import pandas as pd
import streamlit as st

from core import api_client
from core.i18n import fmt_num, lang, t
from core.icons import page_header
from core.ui import api_call, bar, line, metric, show_table

if not api_client.is_admin():  # defence in depth: the API also enforces the admin role
    st.error(t("forbidden"))
    st.stop()

page_header(t("nav_billing"), "file-text", t("billing_intro"))
token = st.session_state["token"]

options = api_call(api_client.cached_get, "/filters", token, lang=lang())
if not options:
    st.stop()
dmin, dmax = date.fromisoformat(options["date_min"]), date.fromisoformat(options["date_max"])

f1, f2 = st.columns([2, 2])
period = f1.date_input(t("billing_period"), value=(dmin, dmax), min_value=dmin, max_value=dmax,
                       format="DD/MM/YYYY", key="billing_period")
start, end = period if isinstance(period, tuple) and len(period) == 2 else (dmin, dmax)
params = {"start": start.isoformat(), "end": end.isoformat()}

# The unfiltered summary also carries the insurer list for the filter (both cached server-side).
overall = api_call(api_client.cached_get, "/billing/summary", token, **params)
if not overall:
    st.stop()
insurer = f2.selectbox(t("insurer"), [None, *overall["insurers"]], format_func=lambda v: v or t("all"),
                       key="billing_insurer")
data = overall if insurer is None else api_call(api_client.cached_get, "/billing/summary", token, insurer=insurer, **params)
if not data:
    st.stop()

totals, currency = data["totals"], data["currency"]
c1, c2, c3, c4 = st.columns(4)
with c1:
    metric(t("billing_admissions"), fmt_num(totals["admissions"], 0))
with c2:
    metric(t("billing_service_lines"), fmt_num(totals["service_lines"], 0))
with c3:
    metric(t("billing_med_units"), fmt_num(totals["medication_units"], 0))
with c4:
    amount = totals["estimated_amount"]
    metric(t("billing_amount"), f"{currency} {fmt_num(amount, 0)}" if amount is not None else "—",
           t("billing_estimated") if data["priced"] else t("billing_no_tariffs"))

value_column = "amount" if data["priced"] else "services_lines"
by_insurer = pd.DataFrame(data["by_insurer"])
bar(by_insurer, "insurer", value_column, t("billing_by_insurer"), horizontal=True)

monthly = pd.DataFrame(data["monthly"])
if not monthly.empty:
    line(monthly, "month", "units", t("billing_monthly"), color="kind")

left, right = st.columns(2, gap="large")
with left:
    st.subheader(t("billing_by_regime"))
    show_table(pd.DataFrame(data["by_regime"]).drop(columns=[] if data["priced"] else ["amount"], errors="ignore"))
with right:
    st.subheader(t("billing_top_items"))
    items = pd.DataFrame(data["top_items"])
    columns = ["kind", "code", "name", "units", "lines", *(["amount"] if data["priced"] else [])]
    show_table(items[[c for c in columns if c in items]] if not items.empty else items)

st.caption(t("billing_note"))
