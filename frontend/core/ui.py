"""Reusable UI pieces: tables, charts, alert cards, conclusions and error handling."""
from html import escape
from typing import Optional

import pandas as pd
import plotly.express as px
import streamlit as st

from core.api_client import ApiError
from core.i18n import col_label, fmt_num, t, value_label
from core.icons import svg
from core.theme import uses_html_tables

CHART_HEIGHT = 380


def localize(df: pd.DataFrame) -> pd.DataFrame:
    """Translates known values (shift, ICD chapter, role) and column names."""
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == object:
            out[col] = out[col].map(value_label)
    return out.rename(columns={c: col_label(c) for c in out.columns})


def show_table(df: pd.DataFrame, height: Optional[int] = None) -> None:
    if df is None or df.empty:
        st.caption("—")
        return
    shown = localize(df)
    if uses_html_tables() and len(shown) <= 200:
        st.table(shown)
    else:
        st.dataframe(shown, hide_index=True, use_container_width=True)


def bar(df: pd.DataFrame, x: str, y: str, title: str, horizontal: bool = False, color: Optional[str] = None) -> None:
    if df is None or df.empty:
        return
    data = df.copy()
    data[x] = data[x].map(value_label).astype(str)
    if horizontal:
        fig = px.bar(data.iloc[::-1], x=y, y=x, orientation="h", title=title, color=color,
                     labels={x: col_label(x), y: col_label(y)})
    else:
        fig = px.bar(data, x=x, y=y, title=title, color=color, barmode="group",
                     labels={x: col_label(x), y: col_label(y)})
    fig.update_layout(height=max(CHART_HEIGHT, 28 * len(data) + 120) if horizontal else CHART_HEIGHT)
    st.plotly_chart(fig, use_container_width=True, config={"displaylogo": False})


def line(df: pd.DataFrame, x: str, y: str, title: str, color: Optional[str] = None,
         threshold: Optional[float] = None) -> None:
    if df is None or df.empty:
        return
    fig = px.line(df, x=x, y=y, color=color, title=title, labels={x: col_label(x), y: col_label(y)})
    fig.update_traces(line={"width": 3})
    if threshold is not None:
        fig.add_hline(y=threshold, line_dash="dash", annotation_text=f"{threshold:g}%", annotation_position="top left")
    fig.update_layout(height=CHART_HEIGHT)
    st.plotly_chart(fig, use_container_width=True, config={"displaylogo": False})


def chart_from_spec(df: pd.DataFrame, spec: Optional[dict]) -> None:
    """Auto chart for agent answers: spec = {"type": "bar|line|pie", "x": col, "y": col}."""
    if not spec or df is None or df.empty or spec["x"] not in df or spec["y"] not in df:
        return
    if spec["type"] == "line":
        line(df, spec["x"], spec["y"], "")
    elif spec["type"] == "pie":
        fig = px.pie(df, names=spec["x"], values=spec["y"])
        fig.update_layout(height=CHART_HEIGHT)
        st.plotly_chart(fig, use_container_width=True, config={"displaylogo": False})
    else:
        bar(df.head(25), spec["x"], spec["y"], "", horizontal=df[spec["x"]].astype(str).str.len().max() > 18)


def alert_card(alert: dict) -> None:
    severity = alert.get("severity", "medium")
    notified = t("notified") if alert.get("notified") else ""
    st.markdown(
        f'<div class="alert-card sev-{severity}" role="status">'
        f'<span class="sev">{svg("alert", 18)}{escape(t("severity_" + severity))}</span>'
        f'<p>{escape(alert["message"])}</p><p class="rec">{escape(alert.get("recommendation", ""))}</p>'
        f'{f"<p class=muted><small>{escape(notified)}</small></p>" if notified else ""}</div>',
        unsafe_allow_html=True,
    )


def conclusion(text: Optional[str]) -> None:
    if text:
        st.markdown(f'<div class="conclusion">{escape(text)}</div>', unsafe_allow_html=True)


def metric(label: str, value, help_text: Optional[str] = None) -> None:
    st.metric(label, value if value is not None else "—", help=help_text)


def api_call(func, *args, **kwargs):
    """Runs an API call and shows a readable message instead of a stack trace."""
    try:
        return func(*args, **kwargs)
    except ApiError as exc:
        if exc.status == 401:
            st.warning(exc.detail)
            st.rerun()
        st.error(exc.detail)
        return None


def minutes(value) -> str:
    return f"{fmt_num(value)} min" if value is not None else "—"
