"""Inferential statistics reports, generated ON DEMAND.

Nothing is computed when the page opens: the user picks the analyses they need and the
backend computes only those sections (each one is cached server-side per dataset).
"""
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import api_client
from core.i18n import lang, t
from core.icons import page_header
from core.theme import palette
from core.ui import api_call, conclusion, line

# Section id (backend) -> title key (i18n). Order = order of the report.
SECTIONS = {
    "wait_time_ci": "rep_wait",
    "occupancy_ci": "rep_occupancy",
    "month_comparison": "rep_compare",
    "trends": "rep_trends",
    "root_cause": "rep_root",
}
OCCUPANCY_TARGET = 85

page_header(t("nav_reports"), "trending-up", t("reports_intro"))


def fetch(sections: list[str]) -> None:
    report = api_call(api_client.get, "/reports/inferential", sections=sections)
    if report:
        st.session_state["report"] = {"lang": lang(), "sections": sections, "data": report}


with st.form("report_request", border=True):
    chosen = st.multiselect(t("rep_choose"), list(SECTIONS), format_func=lambda s: t(SECTIONS[s]),
                            placeholder=t("rep_choose_placeholder"),
                            default=(st.session_state.get("report") or {}).get("sections", []))
    if st.form_submit_button(t("rep_generate"), type="primary"):
        if chosen:
            with st.spinner(t("processing")):
                fetch(chosen)
        else:
            st.warning(t("rep_choose_one"))

state = st.session_state.get("report")
if not state:
    st.info(t("rep_empty"))
    st.stop()
if state["lang"] != lang():  # conclusions are written by the backend: fetch them in the new language
    fetch(state["sections"])
    state = st.session_state["report"]
report = state["data"]


# ---------- Section renderers ----------
def render_wait(section: dict) -> list[str]:
    conclusion(section.get("conclusion"))
    if "ci95_mean" in section:
        st.caption(f"{t('rep_method')}: t de Student (media) · bootstrap percentil, 2000 remuestreos (mediana)"
                   if lang() == "es" else f"{t('rep_method')}: Student's t (mean) · percentile bootstrap, 2000 resamples (median)")
    return [section.get("conclusion")]


def render_occupancy(rows: list[dict]) -> list[str]:
    if not rows:
        st.caption("—")
        return []
    df = pd.DataFrame(rows)
    p = palette()
    fig = go.Figure(go.Scatter(
        x=df["mean_pct"], y=df["bed_group"], mode="markers",
        marker={"size": 12, "color": p["primary"]},
        error_x={"type": "data", "symmetric": False,
                 "array": [hi - m for (lo, hi), m in zip(df["ci95"], df["mean_pct"])],
                 "arrayminus": [m - lo for (lo, hi), m in zip(df["ci95"], df["mean_pct"])], "thickness": 2},
    ))
    fig.add_vline(x=OCCUPANCY_TARGET, line_dash="dash", annotation_text=f"{OCCUPANCY_TARGET}%")
    fig.update_layout(title=t("mean_ci"), height=90 + 45 * len(df), xaxis_title="%", yaxis_title="")
    st.plotly_chart(fig, use_container_width=True, config={"displaylogo": False})
    for row in rows:
        conclusion(row["conclusion"])
    return [row["conclusion"] for row in rows]


def render_comparison(section: dict) -> list[str]:
    conclusion(section.get("conclusion"))
    return [section.get("conclusion")]


def render_trends(section: dict) -> list[str]:
    texts = [section.get("conclusion"), *(item["conclusion"] for item in section["trends"])]
    for text in texts:
        conclusion(text)
    if section["trends"]:
        rows = [{"week": i + 1, "admissions": v, "label": item["label"]}
                for item in section["trends"][:6] for i, v in enumerate(item["weekly_admissions"])]
        line(pd.DataFrame(rows), "week", "admissions", t("weekly_admissions"), color="label")
    return texts


def render_root_cause(section: dict) -> list[str]:
    texts = [*(finding["conclusion"] for finding in section.get("findings", [])), section.get("conclusion")]
    for text in texts:
        conclusion(text)
    return texts


RENDERERS = {
    "wait_time_ci": render_wait,
    "occupancy_ci": render_occupancy,
    "month_comparison": render_comparison,
    "trends": render_trends,
    "root_cause": render_root_cause,
}

reference = datetime.fromisoformat(report["reference_date"]).strftime("%d/%m/%Y %H:%M")
st.caption(t("rep_generated", d=reference))
markdown = [f"# {t('nav_reports')} — {reference}"]
for name in report.get("sections", []):
    st.subheader(t(SECTIONS[name]))
    texts = RENDERERS[name](report[name])
    markdown += [f"\n## {t(SECTIONS[name])}", *(f"- {text}" for text in texts if text)]

st.download_button(t("rep_download"), "\n".join(markdown), file_name=f"reporte_{report['reference_date'][:10]}.md",
                   mime="text/markdown")
