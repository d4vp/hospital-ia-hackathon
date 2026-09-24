"""Conversational agent: natural-language answers, tables and automatic charts.

Generated database queries are never shown (nor returned by the API) to any role:
they are kept only in the server-side audit log.
"""
import pandas as pd
import streamlit as st

from core import api_client
from core.i18n import lang, t
from core.icons import page_header
from core.loader import ambulance_loader
from core.ui import api_call, chart_from_spec, show_table

ENGINE_LABELS = {"llm": "engine_llm", "fallback": "engine_fallback", "guard": "engine_guard"}

page_header(t("nav_agent"), "message", t("agent_intro"))
st.session_state.setdefault("messages", [])

if st.button(f":material/add_comment: {t('new_conversation')}"):
    st.session_state["messages"] = []
    st.session_state.pop("conversation_id", None)
    st.rerun()


def render_answer(message: dict) -> None:
    st.markdown(message["content"])
    table = message.get("table")
    if table:
        df = pd.DataFrame(table["rows"], columns=table["columns"])
        chart_from_spec(df, message.get("chart"))
        with st.expander(t("result_table")):
            show_table(df)
    engine = message.get("engine")
    if engine in ENGINE_LABELS:
        st.caption(t(ENGINE_LABELS[engine]))


for message in st.session_state["messages"]:
    with st.chat_message(message["role"]):
        if message["role"] == "user":
            st.markdown(message["content"])
        else:
            render_answer(message)

if not st.session_state["messages"]:
    cols = st.columns(2)
    for i, key in enumerate(("q1", "q2", "q3", "q4")):
        if cols[i % 2].button(t(key), key=f"suggest_{key}", use_container_width=True):
            st.session_state["pending_question"] = t(key)
            st.rerun()

question = st.chat_input(t("chat_placeholder"), max_chars=1000) or st.session_state.pop("pending_question", None)
if question:
    st.session_state["messages"].append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        with ambulance_loader(t("thinking")):
            payload = {"question": question, "language": lang(),
                       "conversation_id": st.session_state.get("conversation_id")}
            response = api_call(api_client.post, "/chat", payload, timeout=120)
        if response:
            st.session_state["conversation_id"] = response["conversation_id"]
            message = {"role": "assistant", "content": response["answer"], "table": response.get("table"),
                       "chart": response.get("chart"), "engine": response.get("engine")}
            st.session_state["messages"].append(message)
            render_answer(message)
