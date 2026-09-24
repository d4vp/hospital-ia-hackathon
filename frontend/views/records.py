"""Admin-only clinical record entry: admissions, services and medications.

Records are written to the hospital system of record (SQL Server) first and then projected to
MongoDB with the same rules as the workbook ETL; if MongoDB is temporarily unavailable the
record waits in a sync queue (tab "Sincronización"). The choice lists come from the loaded
data, so the forms can only produce values the analytics already understand.
"""
from datetime import date, datetime, time, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from core import api_client
from core.i18n import t
from core.icons import page_header
from core.loader import ambulance_loader
from core.ui import api_call, show_table

if not api_client.is_admin():  # defence in depth: the API also enforces the admin role
    st.error(t("forbidden"))
    st.stop()

DOCUMENT_TYPES = ("CC", "TI", "RC", "CE", "PA")
TRIAGE_CATEGORIES = ("ADULTOS", "PEDIATRIA", "GINECOOBSTETRICIA")

page_header(t("nav_records"), "file-text", t("records_intro"))
catalog = api_call(api_client.get, "/records/catalog")
if not catalog:
    st.stop()
st.caption(t("records_mode_" + catalog["mode"]))
if flash := st.session_state.pop("records_flash", None):
    st.success(flash)


def hospital_now() -> datetime:
    """Local time of the hospital (the backend validates dates in that time zone)."""
    return datetime.now(ZoneInfo(catalog["timezone"])).replace(tzinfo=None, second=0, microsecond=0)


def when(label: str, key: str, default: Optional[datetime] = None) -> str:
    """Date + time pickers -> ISO string, limited to the allowed back-dating window."""
    default = default or hospital_now()
    today = default.date()
    c1, c2 = st.columns(2)
    day = c1.date_input(label, value=default.date(), min_value=today - timedelta(days=catalog["max_backdate_days"]),
                        max_value=today, format="DD/MM/YYYY", key=f"{key}_d")
    hour = c2.time_input(t("time"), value=default.time(), step=timedelta(minutes=5), key=f"{key}_t")
    return datetime.combine(day, hour or time()).isoformat()


def choose(label: str, options: list[Any], key: str) -> Any:
    if options:
        return st.selectbox(label, options, key=key)
    return st.text_input(label, key=key)


def submit(path: str, payload: dict[str, Any]) -> None:
    with ambulance_loader(t("saving_record")):
        result = api_call(api_client.post, path, payload)
    if result:
        api_client.live_get.clear()
        st.cache_data.clear()  # KPIs and alerts must include the new record
        status_text = t("record_synced") if result["status"] == "synced" else t("record_pending")
        ids = ", ".join(f"{k}={v}" for k, v in result.items() if k.endswith("_id") and v is not None)
        st.session_state["records_flash"] = f"{t('record_saved')} ({ids}). {status_text}"
        st.rerun()


tab_admission, tab_service, tab_medication, tab_sync = st.tabs(
    [t("tab_new_admission"), t("tab_new_service"), t("tab_new_medication"), t("tab_sync")])

# ---------- Admission ----------
with tab_admission:
    new_patient = st.toggle(t("new_patient"), key="rec_new_patient")
    with_triage = st.toggle(t("with_triage"), key="rec_with_triage")
    with_attention = st.toggle(t("with_attention"), key="rec_with_attention")
    with st.form("record_admission", border=True):
        patient_id = st.number_input(t("patient_id"), min_value=1, step=1, format="%d", key="rec_patient_id")
        patient: Optional[dict[str, Any]] = None
        if new_patient:
            st.markdown(f"**{t('patient_data')}**")
            p1, p2, p3 = st.columns(3)
            patient = {
                "document_type": p1.selectbox(t("document_type"), DOCUMENT_TYPES, key="rec_doc"),
                "full_name": p2.text_input(t("full_name"), max_chars=120, key="rec_name"),
                "birth_date": p3.date_input(t("birth_date"), value=date(1990, 1, 1), min_value=date(1900, 1, 1),
                                            max_value=hospital_now().date(), format="DD/MM/YYYY", key="rec_birth").isoformat(),
                "sex": p1.selectbox(t("sex"), catalog["patient_sex"] or ["Femenino", "Masculino"], key="rec_sex"),
                "insurer": p2.text_input(t("insurer"), max_chars=160, key="rec_insurer"),
                "regime": choose(t("regime"), catalog["patient_regime"], "rec_regime"),
                "department": p3.text_input(t("department"), value="CAUCA", max_chars=80, key="rec_dep"),
                "municipality": p1.text_input(t("municipality"), value="POPAYÁN", max_chars=80, key="rec_mun"),
                "zone": p2.selectbox(t("zone"), catalog["patient_zone"] or ["Urbana", "Rural"], key="rec_zone"),
            }
        st.markdown(f"**{t('admission_data')}**")
        a1, a2, a3 = st.columns(3)
        with a1:
            admission_class = choose(t("admission_class"), catalog["admission_class"], "rec_class")
        with a2:
            admission_route = choose(t("admission_route"), catalog["admission_route"], "rec_route")
        with a3:
            risk_type = choose(t("risk_type"), catalog["risk_type"], "rec_risk")
        admission_date = when(t("admission_date"), "rec_adm")
        attention_date = when(t("attention_date"), "rec_att") if with_attention else None
        b1, b2, b3 = st.columns(3)
        bed_group = b1.selectbox(t("bed_group"), catalog["bed_groups"], key="rec_bed_group")
        bed_code = b2.text_input(t("bed_code"), max_chars=20, key="rec_bed_code")
        bed_name = b3.text_input(t("bed_name"), max_chars=80, key="rec_bed_name")
        d1, d2 = st.columns([1, 3])
        diagnosis_code = d1.text_input(t("diagnosis_code"), max_chars=6, help=t("diagnosis_help"), key="rec_dx")
        diagnosis_name = d2.text_input(t("diagnosis_name"), max_chars=200, key="rec_dx_name")
        triage: Optional[dict[str, Any]] = None
        if with_triage:
            st.markdown(f"**{t('triage_data')}**")
            t1, t2, t3 = st.columns(3)
            triage = {
                "triage_date": admission_date,
                "level": t1.selectbox(t("triage_level"), [1, 2, 3, 4, 5], index=2, key="rec_tri_level"),
                "category": t2.selectbox(t("triage_category"), TRIAGE_CATEGORIES, key="rec_tri_cat"),
                "chief_complaint": t3.text_input(t("chief_complaint"), max_chars=300, key="rec_tri_cc"),
                "blood_pressure": t1.text_input(t("blood_pressure"), placeholder="120/80", key="rec_tri_bp") or None,
                "heart_rate": t2.number_input(t("heart_rate"), 20, 250, 80, key="rec_tri_hr"),
                "temperature": t3.number_input(t("temperature"), 30.0, 45.0, 36.5, step=0.1, key="rec_tri_temp"),
            }
        if st.form_submit_button(t("save_record"), type="primary", use_container_width=True):
            submit("/records/admissions", {
                "patient_id": int(patient_id), "patient": patient, "admission_class": admission_class,
                "admission_route": admission_route, "risk_type": risk_type, "admission_date": admission_date,
                "attention_date": attention_date, "bed_group": bed_group, "bed_code": bed_code.strip(),
                "bed_name": bed_name.strip(), "diagnosis_code": diagnosis_code.strip().upper(),
                "diagnosis_name": diagnosis_name.strip(), "triage": triage,
            })


# ---------- Lines ----------
def line_form(kind: str, path: str, areas: list[str]) -> None:
    with st.form(f"record_{kind}", border=True):
        l1, l2, l3 = st.columns([1.2, 1, 2])
        admission_id = l1.number_input(t("admission_id_input"), min_value=1, step=1, format="%d", key=f"rec_{kind}_adm")
        code = l2.text_input(t("cups_code") if kind == "service" else t("med_code"), max_chars=20, key=f"rec_{kind}_code")
        name = l3.text_input(t("item_name"), max_chars=200, key=f"rec_{kind}_name")
        q1, q2, q3 = st.columns(3)
        quantity = q1.number_input(t("quantity_input"), min_value=1, max_value=10_000, value=1, key=f"rec_{kind}_qty")
        with q2:
            area = choose(t("area_input"), areas, f"rec_{kind}_area")
        with q3:
            specialty = choose(t("specialty"), catalog["services_specialty"], f"rec_{kind}_spec")
        service_date = when(t("service_date"), f"rec_{kind}_date")
        if st.form_submit_button(t("save_record"), type="primary", use_container_width=True):
            submit(path, {"admission_id": int(admission_id), "code": code.strip(), "name": name.strip(),
                          "quantity": int(quantity), "service_date": service_date, "area": area,
                          "specialty": specialty})


with tab_service:
    line_form("service", "/records/services", catalog["services_area"])
with tab_medication:
    line_form("medication", "/records/medications", catalog["medications_area"] or catalog["services_area"])

# ---------- Sync queue ----------
with tab_sync:
    st.caption(t("sync_intro"))
    sync = api_call(api_client.get, "/records/sync")
    if sync:
        c1, c2, c3 = st.columns(3)
        c1.metric(t("sync_pending"), sync["counts"]["pending"])
        c2.metric(t("sync_failed"), sync["counts"]["failed"])
        c3.metric(t("sync_done"), sync["counts"]["done"])
        if sync["recent"]:
            df = pd.DataFrame(sync["recent"])
            show_table(df[[c for c in ("created_at", "kind", "status", "attempts", "last_error") if c in df]])
    if st.button(f":material/sync: {t('sync_retry')}"):
        with ambulance_loader(t("processing")):
            result = api_call(api_client.post, "/records/sync/retry")
        if result:
            st.session_state["records_flash"] = t("sync_retried", **result)
            st.rerun()
