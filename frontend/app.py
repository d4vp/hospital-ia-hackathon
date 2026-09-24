"""Streamlit entry point: session, accessibility settings, login and role-based navigation."""
import os

import streamlit as st
from streamlit.errors import StreamlitAPIException

from core import api_client
from core.api_client import ApiError
from core.i18n import LANGUAGES, t
from core.icons import page_header, svg
from core.theme import apply_theme

try:
    st.set_page_config(page_title="Hospital Susana López de Valencia", page_icon=":material/local_hospital:",
                       layout="wide", initial_sidebar_state="auto")
except StreamlitAPIException:
    st.set_page_config(page_title="Hospital Susana López de Valencia", layout="wide")

# ---------- Session defaults ----------
st.session_state.setdefault("lang", os.getenv("DEFAULT_LANGUAGE", "es") if os.getenv("DEFAULT_LANGUAGE") in LANGUAGES else "es")
st.session_state.setdefault("theme", "light")
st.session_state.setdefault("font_scale", 1.0)

apply_theme()


def accessibility_panel() -> None:
    with st.sidebar:
        st.markdown(
            f'<div class="brand">{svg("cross", 30)}<div><strong>{t("app_name")}</strong>'
            f'<small>{t("app_tagline")}</small></div></div>',
            unsafe_allow_html=True,
        )
        with st.expander(t("a11y_title"), expanded=False, key="expander_accessibility"):
            st.radio(t("language"), options=list(LANGUAGES), format_func=LANGUAGES.get, key="lang", horizontal=True)
            st.radio(t("theme"), options=["light", "dark", "high_contrast"], format_func=lambda v: t(f"theme_{v}"),
                     key="theme")
            st.slider(t("font_size"), min_value=0.9, max_value=1.6, step=0.1, key="font_scale", format="%.1fx")
        user = st.session_state.get("user")
        if user:
            st.caption(f"{t('signed_in_as')}: **{user.get('full_name') or user['email']}** · {t('role_' + user['role'])}")
            if st.button(t("logout"), use_container_width=True):
                api_client.logout()
                st.rerun()


def login_view() -> None:
    page_header(t("nav_login"), "lock", t("login_title"))
    col, _ = st.columns([1, 1])
    with col, st.form("login", border=True):
        email = st.text_input(t("email"), autocomplete="username")
        password = st.text_input(t("password"), type="password", autocomplete="current-password")
        if st.form_submit_button(t("login_button"), use_container_width=True):
            try:
                api_client.login(email.strip(), password)
                st.rerun()
            except ApiError as exc:
                st.error(t("login_failed") if exc.status == 401 else exc.detail)
    st.caption(t("login_help"))


accessibility_panel()

if not st.session_state.get("token"):
    navigation = st.navigation([st.Page(login_view, title=t("nav_login"), icon=":material/login:", url_path="login")])
else:
    pages = {
        "": [st.Page("views/home.py", title=t("nav_home"), icon=":material/home:", default=True)],
        t("section_analysis"): [
            st.Page("views/agent.py", title=t("nav_agent"), icon=":material/forum:", url_path="agent"),
            st.Page("views/dashboard.py", title=t("nav_dashboard"), icon=":material/monitoring:", url_path="dashboard"),
            st.Page("views/reports.py", title=t("nav_reports"), icon=":material/query_stats:", url_path="reports"),
            st.Page("views/alerts.py", title=t("nav_alerts"), icon=":material/notifications:", url_path="alerts"),
        ],
    }
    if api_client.is_admin():  # non-admins never see (or can reach) these pages
        pages[t("section_admin")] = [
            st.Page("views/data_upload.py", title=t("nav_upload"), icon=":material/upload_file:", url_path="data-upload"),
            st.Page("views/admin_users.py", title=t("nav_users"), icon=":material/manage_accounts:", url_path="users"),
        ]
    navigation = st.navigation(pages)

navigation.run()
