"""Single source of truth for the queryable schema.

Used by (1) the agent prompt, (2) the query validator (field whitelist) and
(3) the PII guard. Keep it in sync with data_loader.py.
"""
from app.core.config import PII_PATHS

ADMISSION_FIELDS = {
    "_id": "int, admission id (OidIngreso)",
    "admission_number": "int",
    "admission_class": "string, see real values",
    "admission_route": "string, see real values",
    "risk_type": "string, see real values",
    "admission_date": "datetime",
    "hospitalization_date": "datetime|null",
    "attention_date": "datetime|null, first medical attention",
    "wait_minutes": "float|null, attention_date - admission_date in minutes (precomputed)",
    "shift": "'day' (07:00-18:59) | 'night', based on admission hour",
    "estimated_discharge_date": "datetime, last recorded service/medication (discharge proxy)",
    "length_of_stay_days": "float",
    "currently_admitted": "bool, true if the patient is estimated to be in the bed at the reference date",
    "primary_specialty": "string, most frequent specialty of the admission's services",
    "surgeries_scheduled": "int",
    "surgeries_performed": "int",
    "bed.code": "string",
    "bed.name": "string",
    "bed.group": "string, see real values (this is the 'service' of the admission)",
    "bed.subgroup": "string",
    "diagnosis.chapter": "string, ICD-10 chapter key, see real values",
    "patient.document_type": "string",
    "patient.sex": "string",
    "patient.age": "float, years at admission",
    "patient.age_group": "string",
    "patient.insurer": "string",
    "patient.regime": "string",
    "patient.department": "string",
    "patient.municipality": "string",
    "patient.zone": "string",
    "triage": "object|null",
    "triage.triage_id": "int",
    "triage.triage_date": "datetime",
    "triage.blood_pressure": "string",
    "triage.heart_rate": "float",
    "triage.respiratory_rate": "float",
    "triage.temperature": "float",
    "triage.code": "int, consultation-room code (NOT the triage level)",
    "triage.classification": "string, free text",
    "triage.level": "int 1-5, 1 = most urgent (use this for triage questions)",
    "services": "array",
    "services.line_id": "int",
    "services.cups_code": "string",
    "services.name": "string",
    "services.quantity": "int",
    "services.service_date": "datetime",
    "services.area_code": "int",
    "services.area": "string",
    "services.specialty": "string",
    "medications": "array",
    "medications.line_id": "int",
    "medications.code": "string",
    "medications.name": "string",
    "medications.quantity": "int",
    "medications.service_date": "datetime",
    "medications.area": "string",
    "medications.specialty": "string",
    "scheduled_surgeries": "array",
    "scheduled_surgeries.schedule_number": "string",
    "scheduled_surgeries.cups_code": "string",
    "scheduled_surgeries.performed": "bool",
}

COLLECTION_FIELDS: dict[str, dict[str, str]] = {
    "admissions": ADMISSION_FIELDS,
    "inventory": {
        "_id": "string, medication code",
        "code": "string",
        "name": "string",
        "stock": "int, units on hand (SYNTHETIC)",
        "avg_daily_consumption": "float, real average units per day (last 30 days)",
        "days_of_inventory": "float, stock / avg_daily_consumption",
        "reorder_point": "int",
        "expiration_date": "datetime (SYNTHETIC)",
        "synthetic": "bool, always true",
    },
    "medication_usage_daily": {
        "_id": "objectid",
        "date": "datetime (day)",
        "code": "string",
        "name": "string",
        "quantity": "int, units dispensed that day",
        "lines": "int",
    },
    "service_demand_daily": {
        "_id": "objectid",
        "date": "datetime (day)",
        "specialty": "string",
        "area": "string",
        "lines": "int, number of services provided",
        "quantity": "int",
        "admissions": "int, distinct admissions served that day",
    },
    "bed_capacity": {
        "_id": "string, bed group",
        "group": "string",
        "beds": "int, distinct beds observed (capacity proxy)",
        "virtual_beds": "int",
    },
}

# Fields that exist in the database but must never be queried or returned.
FORBIDDEN_FIELDS = set(PII_PATHS)


def is_known_path(collection: str, path: str, extra: set[str] | None = None) -> bool:
    """True when `path` is a known field, a parent of one, or an element of an array."""
    fields = COLLECTION_FIELDS.get(collection, {})
    extra = extra or set()
    root = path.split(".")[0]
    if path in fields or path in extra or root in extra:
        return True
    return any(f.startswith(path + ".") for f in fields)


def is_forbidden_path(path: str) -> bool:
    return any(path == f or path.startswith(f + ".") for f in FORBIDDEN_FIELDS) or path == "diagnosis"


def schema_for_prompt() -> str:
    lines = []
    for collection, fields in COLLECTION_FIELDS.items():
        lines.append(f"Collection `{collection}`:")
        for name, desc in fields.items():
            lines.append(f"  - {name}: {desc}")
    return "\n".join(lines)
