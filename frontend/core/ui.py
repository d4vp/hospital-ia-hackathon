"""Reusable UI pieces: tables, charts, alert cards, conclusions and error handling."""
from html import escape
from typing import Optional

import pandas as pd
import plotly.express as px
import streamlit as st

from core import api_client
from core.api_client import ApiError
from core.i18n import col_label, fmt_num, lang, t, value_label
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


SEVERITIES = ("critical", "high", "medium")
TICKER_MIN_ITEMS_TO_SLIDE = 3


def workflow_badge(status: Optional[str]) -> str:
    status = status or "new"
    return f'<span class="wf-badge wf-{escape(status)}">{escape(t("workflow_" + status))}</span>'


def alert_card(alert: dict) -> None:
    severity = alert.get("severity", "medium")
    footer = []
    if alert.get("notified"):
        footer.append(t("notified"))
    if alert.get("workflow_updated_by"):
        footer.append(t("last_update_by", status=t("workflow_" + alert.get("workflow_status", "new")),
                        by=alert["workflow_updated_by"]))
    if alert.get("workflow_note"):
        footer.append(f"“{alert['workflow_note']}”")
    footer_html = f'<p class="muted"><small>{escape(" · ".join(footer))}</small></p>' if footer else ""
    attended = " attended" if alert.get("workflow_status") == "in_progress" else ""
    st.markdown(
        f'<div class="alert-card sev-{severity}{attended}" role="status">'
        f'<span class="sev">{svg("alert", 18)}{escape(t("severity_" + severity))}</span>'
        f'{workflow_badge(alert.get("workflow_status"))}'
        f'<p>{escape(alert["message"])}</p><p class="rec">{escape(alert.get("recommendation", ""))}</p>'
        f'{footer_html}</div>',
        unsafe_allow_html=True,
    )


def _ticker_item(item: dict) -> str:
    severity = item.get("severity", "medium")
    figure = f" · {escape(item['headline'])}" if item.get("headline") else ""
    attended = "" if item.get("pending", True) else " attended"
    return (f'<span class="item sev-{severity}{attended}"><b>{escape(t("severity_" + severity))}</b>'
            f'{escape(item.get("subject") or "")}{figure}'
            f'<span class="st">· {escape(t("workflow_" + (item.get("workflow_status") or "new")))}</span></span>')


def alert_ticker(summary: Optional[dict]) -> None:
    """Top-of-page alert bar: PENDING counters by severity, an "in progress" counter and a
    horizontally sliding list of alerts (pending first; attended ones are dimmed).

    Counters only include alerts nobody is attending yet, so they go down as soon as staff
    move an alert to "En progreso". The slide pauses on hover/focus, stops with
    prefers-reduced-motion, and the track can always be scrolled by hand.
    """
    summary = summary or {"pending_by_severity": {}, "items": []}
    counts = summary.get("pending_by_severity", summary.get("by_severity", {}))
    in_progress = int(summary.get("in_progress", 0))
    chips = "".join(
        f'<span class="count sev-{sev}{" zero" if not counts.get(sev) else ""}" title="{escape(t("pending_hint"))}">'
        f'{svg("alert", 15)}{escape(t("count_" + sev))} {int(counts.get(sev, 0))}</span>'
        for sev in SEVERITIES
    ) + (f'<span class="count wip{" zero" if not in_progress else ""}">{svg("activity", 15)}'
         f'{escape(t("workflow_in_progress"))} {in_progress}</span>')
    items = summary.get("items", [])
    if items:
        row = "".join(_ticker_item(item) for item in items)
        moving = len(items) >= TICKER_MIN_ITEMS_TO_SLIDE
        # The list is duplicated (aria-hidden) so the loop is seamless: the animation moves -50%.
        track = (f'<div class="items{" moving" if moving else ""}" style="--ticker-duration:{max(20, 6 * len(items))}s">'
                 f'{row}{f"<span aria-hidden=true style=display:contents>{row}</span>" if moving else ""}</div>')
    else:
        track = f'<span class="empty">{escape(t("alerts_bar_empty"))}</span>'
    st.markdown(
        f'<div class="alert-ticker" role="region" aria-label="{escape(t("alerts_bar_label"))}">'
        f'<div class="counts">{chips}</div><div class="track" tabindex="0">{track}</div></div>',
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


def alert_bar() -> None:
    """Alert ticker at the top of a page, with a button to the Alerts page (keeps the session)."""
    summary = api_call(api_client.live_get, "/alerts/summary", st.session_state["token"], lang=lang())
    bar_col, button_col = st.columns([6, 1], vertical_alignment="center")
    with bar_col:
        alert_ticker(summary)
    with button_col:
        if st.button(f":material/notifications: {t('open_alerts')}", use_container_width=True, key="alert_bar_open"):
            st.switch_page("views/alerts.py")
