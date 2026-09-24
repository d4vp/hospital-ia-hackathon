"""Admin-only user management (create, edit role, deactivate, reset password)."""
import pandas as pd
import streamlit as st

from core import api_client
from core.i18n import t, value_label
from core.icons import page_header
from core.ui import api_call, show_table

if not api_client.is_admin():
    st.error(t("forbidden"))
    st.stop()

page_header(t("nav_users"), "users", t("users_intro"))
if flash := st.session_state.pop("flash", None):
    st.success(flash)
users = api_call(api_client.get, "/users") or []

if users:
    df = pd.DataFrame(users)[["email", "full_name", "role", "is_active", "last_login_at"]]
    df["last_login_at"] = df["last_login_at"].fillna("").astype(str).str[:16].str.replace("T", " ")
    show_table(df)

left, right = st.columns(2, gap="large")
with left, st.form("create_user", clear_on_submit=True, border=True):
    st.subheader(t("create_user"))
    email = st.text_input(t("email"))
    name = st.text_input(t("full_name"))
    password = st.text_input(t("password"), type="password", help=t("password_rule"))
    role = st.radio(t("role"), ["user", "admin"], format_func=value_label, horizontal=True)
    if st.form_submit_button(t("create_user"), use_container_width=True):
        if api_call(api_client.post, "/users", {"email": email, "full_name": name, "password": password, "role": role}):
            st.session_state["flash"] = t("user_created")
            st.rerun()

with right:
    if users:
        st.subheader(t("edit_user"))
        # Outside the form so the fields below refresh when another user is selected.
        selected = st.selectbox(t("select_user"), users, format_func=lambda u: f"{u['email']} ({value_label(u['role'])})")
        locked = bool(selected.get("is_last_admin"))  # the API refuses these changes too (409)
        if locked:
            st.info(t("last_admin_locked"))
        with st.form(f"edit_user_{selected['id']}", border=True):
            new_role = st.radio(t("role"), ["user", "admin"], index=0 if selected["role"] == "user" else 1,
                                format_func=value_label, horizontal=True, disabled=locked)
            active = st.toggle(t("active"), value=selected["is_active"], disabled=locked)
            new_password = st.text_input(t("new_password"), type="password", help=t("password_rule"))
            if st.form_submit_button(t("save_changes"), use_container_width=True):
                changes = {} if locked else {"role": new_role, "is_active": active}
                if new_password:
                    changes["password"] = new_password
                if api_call(api_client.patch, f"/users/{selected['id']}", changes):
                    st.session_state["flash"] = t("user_saved")
                    st.rerun()
