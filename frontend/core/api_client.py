"""Thin HTTP client for the backend API (Bearer token kept in the Streamlit session)."""
import os
from typing import Any, Optional

import requests
import streamlit as st

from core.i18n import lang, t

API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000/api").rstrip("/")


class ApiError(RuntimeError):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _headers() -> dict[str, str]:
    token = st.session_state.get("token")
    return {"Authorization": f"Bearer {token}"} if token else {}


def request(method: str, path: str, *, params: Optional[dict] = None, json: Any = None,
            files: Any = None, timeout: float = 60) -> Any:
    try:
        response = requests.request(method, f"{API_BASE_URL}{path}", params=params, json=json,
                                    files=files, headers=_headers(), timeout=timeout)
    except requests.RequestException as exc:
        raise ApiError(0, t("api_unreachable")) from exc
    if response.status_code == 401 and st.session_state.get("token"):
        logout()
        raise ApiError(401, t("session_expired"))
    if response.status_code >= 400:
        try:
            body = response.json()
            detail = body.get("detail") if isinstance(body, dict) else str(body)
            if isinstance(body, dict) and body.get("code") == "dataset_not_loaded":
                detail = t("no_data_loaded")
        except ValueError:
            detail = response.text[:300]
        raise ApiError(response.status_code, str(detail))
    return response.json() if response.content else None


def get(path: str, **params) -> Any:
    clean = {k: v for k, v in params.items() if v not in (None, "")}
    clean.setdefault("lang", lang())
    return request("GET", path, params=clean)


def post(path: str, payload: Any = None, **kwargs) -> Any:
    return request("POST", path, json=payload, **kwargs)


def patch(path: str, payload: Any) -> Any:
    return request("PATCH", path, json=payload)


@st.cache_data(ttl=300, show_spinner=False)
def cached_get(path: str, token: str, **params) -> Any:
    """Cached GET for heavy read-only endpoints. `token` is part of the cache key."""
    return get(path, **params)


@st.cache_data(ttl=30, show_spinner=False)
def live_get(path: str, token: str, **params) -> Any:
    """Short-lived cache for data that staff change (alerts): cleared after every status update."""
    return get(path, **params)


@st.cache_data(ttl=300, show_spinner=False)
def features() -> dict:
    """Optional modules enabled on the backend (public /health endpoint)."""
    try:
        return (request("GET", "/health", timeout=5) or {}).get("features", {})
    except ApiError:
        return {}


def login(email: str, password: str) -> dict:
    data = request("POST", "/auth/login", json={"email": email, "password": password})
    st.session_state["token"] = data["access_token"]
    st.session_state["user"] = data["user"]
    return data["user"]


def logout() -> None:
    for key in ("token", "user", "messages", "conversation_id"):
        st.session_state.pop(key, None)
    st.cache_data.clear()


def is_admin() -> bool:
    return (st.session_state.get("user") or {}).get("role") == "admin"
