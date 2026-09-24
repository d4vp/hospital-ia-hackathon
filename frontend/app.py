"""Streamlit entry point: session, accessibility menu, login and role-based navigation."""
import os

import streamlit as st
from streamlit.errors import StreamlitAPIException

from core import api_client
from core.api_client import ApiError
from core.i18n import LANGUAGES, t
from core.icons import page_header, svg
from core.theme import THEMES, apply_theme

try:
    st.set_page_config(page_title="Hospital Susana López de Valencia", page_icon=":material/local_hospital:",
                       layout="wide", initial_sidebar_state="auto")
except StreamlitAPIException:
    st.set_page_config(page_title="Hospital Susana López de Valencia", layout="wide")

# ---------- Session defaults ----------
st.session_state.setdefault("lang", os.getenv("DEFAULT_LANGUAGE", "es") if os.getenv("DEFAULT_LANGUAGE") in LANGUAGES else "es")
st.session_state.setdefault("theme", "light")
st.session_state.pop("font_scale", None)  # text size now follows the browser zoom

apply_theme()


def accessibility_menu() -> None:
    """Small floating menu at the top-right corner (see `[data-testid="stPopover"]` in theme.py)."""
    with st.popover(f":material/settings_accessibility: {t('a11y_title')}"):
        st.radio(t("language"), options=list(LANGUAGES), format_func=LANGUAGES.get, key="lang", horizontal=True)
        st.radio(t("theme"), options=THEMES, format_func=lambda v: t(f"theme_{v}"), key="theme")


def sidebar() -> None:
    with st.sidebar:
        st.markdown(
            f'<div class="brand">{svg("cross", 30)}<div><strong>{t("app_name")}</strong>'
            f'<small>{t("app_tagline")}</small></div></div>',
            unsafe_allow_html=True,
        )
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


accessibility_menu()
sidebar()

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
        admin_pages = [
            st.Page("views/data_upload.py", title=t("nav_upload"), icon=":material/upload_file:", url_path="data-upload"),
            st.Page("views/admin_users.py", title=t("nav_users"), icon=":material/manage_accounts:", url_path="users"),
        ]
        admin_pages.insert(0, st.Page("views/records.py", title=t("nav_records"), icon=":material/edit_note:",
                                      url_path="records"))
        if api_client.features().get("billing"):  # optional module, switched on/off in the backend
            admin_pages.insert(0, st.Page("views/billing.py", title=t("nav_billing"), icon=":material/receipt_long:",
                                          url_path="billing"))
        pages[t("section_admin")] = admin_pages
    navigation = st.navigation(pages)

navigation.run()
