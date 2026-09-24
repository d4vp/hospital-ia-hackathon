"""Conversational agent: natural-language answers, tables and automatic charts.

The technical detail (generated query, errors, timings) is returned by the API only to
administrators and is shown folded in an expander.
"""
import pandas as pd
import streamlit as st

from core import api_client
from core.i18n import lang, t
from core.icons import page_header
from core.ui import api_call, chart_from_spec, show_table

page_header(t("nav_agent"), "message", t("agent_intro"))
st.session_state.setdefault("messages", [])

if st.button(t("new_conversation"), icon=":material/add_comment:"):
    st.session_state["messages"] = []
    st.session_state.pop("conversation_id", None)
    st.rerun()


def render_answer(message: dict) -> None:
    st.markdown(message["content"])
    table = message.get("table")
    if table:
        df = pd.DataFrame(table["rows"], columns=table["columns"])
        chart_from_spec(df, message.get("chart"))
        with st.expander(t("result_table"), icon=":material/table:"):
            show_table(df)
    engine = message.get("engine")
    if engine:
        st.caption(t("engine_llm") if engine == "llm" else t("engine_fallback"))
    if message.get("debug"):
        with st.expander(t("technical_detail"), icon=":material/code:"):
            st.json(message["debug"], expanded=False)


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
        with st.spinner(t("thinking")):
            payload = {"question": question, "language": lang(),
                       "conversation_id": st.session_state.get("conversation_id")}
            response = api_call(api_client.post, "/chat", payload, timeout=120)
        if response:
            st.session_state["conversation_id"] = response["conversation_id"]
            message = {"role": "assistant", "content": response["answer"], "table": response.get("table"),
                       "chart": response.get("chart"), "engine": response.get("engine"), "debug": response.get("debug")}
            st.session_state["messages"].append(message)
            render_answer(message)
