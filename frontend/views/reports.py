"""Inferential statistics reports with plain-language conclusions."""
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import api_client
from core.i18n import lang, t
from core.icons import page_header
from core.theme import palette
from core.ui import api_call, conclusion, line

page_header(t("nav_reports"), "trending-up", t("reports_intro"))
report = api_call(api_client.cached_get, "/reports/inferential", st.session_state["token"], lang=lang())
if not report:
    st.stop()

st.subheader(t("rep_wait"))
wait = report["wait_time_ci"]
conclusion(wait.get("conclusion"))
if "ci95_mean" in wait:
    st.caption(f"{t('rep_method')}: t de Student (media) · bootstrap percentil, 2000 remuestreos (mediana)"
               if lang() == "es" else f"{t('rep_method')}: Student's t (mean) · percentile bootstrap, 2000 resamples (median)")

st.subheader(t("rep_occupancy"))
occ = report["occupancy_ci"]
if occ:
    df = pd.DataFrame(occ)
    p = palette()
    fig = go.Figure(go.Scatter(
        x=df["mean_pct"], y=df["bed_group"], mode="markers",
        marker={"size": 12, "color": p["primary"]},
        error_x={"type": "data", "symmetric": False,
                 "array": [hi - m for (lo, hi), m in zip(df["ci95"], df["mean_pct"])],
                 "arrayminus": [m - lo for (lo, hi), m in zip(df["ci95"], df["mean_pct"])], "thickness": 2},
    ))
    fig.add_vline(x=85, line_dash="dash", annotation_text="85%")
    fig.update_layout(title=t("mean_ci"), height=90 + 45 * len(df), xaxis_title="%", yaxis_title="")
    st.plotly_chart(fig, use_container_width=True, config={"displaylogo": False})
    for row in occ:
        conclusion(row["conclusion"])

st.subheader(t("rep_compare"))
conclusion(report["month_comparison"].get("conclusion"))

st.subheader(t("rep_trends"))
trends = report["trends"]
conclusion(trends.get("conclusion"))
for item in trends["trends"]:
    conclusion(item["conclusion"])
if trends["trends"]:
    rows = [{"week": i + 1, "admissions": v, "label": item["label"]}
            for item in trends["trends"][:6] for i, v in enumerate(item["weekly_admissions"])]
    line(pd.DataFrame(rows), "week", "admissions", t("weekly_admissions"), color="label")

st.subheader(t("rep_root"))
for finding in report["root_cause"].get("findings", []):
    conclusion(finding["conclusion"])
conclusion(report["root_cause"].get("conclusion"))
