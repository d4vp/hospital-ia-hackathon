"""Admin-only: upload the HIS workbook or reprocess the server copy."""
import pandas as pd
import streamlit as st

from core import api_client
from core.i18n import t
from core.icons import page_header
from core.ui import api_call, show_table

if not api_client.is_admin():  # defence in depth: the API also enforces the admin role
    st.error(t("forbidden"))
    st.stop()

page_header(t("nav_upload"), "upload", t("upload_intro"))


def _done(summary: dict) -> None:
    st.cache_data.clear()
    st.success(t("load_done", n=summary["admissions_processed"], d=str(summary["reference_date"])[:16]))
    st.json(summary, expanded=False)


with st.form("upload", border=True):
    file = st.file_uploader(t("choose_file"), type=["xlsx"])
    submitted = st.form_submit_button(t("upload_button"), use_container_width=True)
if submitted and file is not None:
    with st.spinner(t("processing")):
        summary = api_call(api_client.request, "POST", "/upload-data",
                           files={"file": (file.name, file.getvalue(),
                                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                           timeout=900)
    if summary:
        _done(summary)

if st.button(f":material/refresh: {t('reload_button')}"):
    with st.spinner(t("processing")):
        summary = api_call(api_client.request, "POST", "/load-data", timeout=900)
    if summary:
        _done(summary)

status = api_call(api_client.get, "/data/status")
if status and status.get("loaded"):
    st.subheader(t("current_dataset"))
    show_table(pd.DataFrame([status["counts"]]))
    st.subheader(t("quality"))
    quality = {k: v for k, v in status["quality"].items() if not isinstance(v, dict)}
    show_table(pd.DataFrame([quality]))
