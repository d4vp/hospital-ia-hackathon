"""Validated input for records inserted from the application.

Field names follow the app (English); `to_his_row()` maps them to the HIS / SQL Server
column names (the same ones the Excel export and the ETL use), so a record inserted here
and a record loaded from the workbook produce exactly the same MongoDB document.

Temporal rules: no future dates (hospital local time) and no back-dating beyond
RECORDS_MAX_BACKDATE_DAYS; attention / hospitalization / service dates cannot precede the
admission.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal, Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.config import settings
from app.services.text_utils import triage_level

FUTURE_TOLERANCE = timedelta(minutes=5)
TRIAGE_CATEGORIES = ("ADULTOS", "PEDIATRIA", "GINECOOBSTETRICIA")

Code = Field(min_length=1, max_length=20, pattern=r"^[0-9A-Za-z][0-9A-Za-z._-]*$")
Text = Field(min_length=1, max_length=160)


def now_local() -> datetime:
    """Current time in the hospital time zone, naive (the HIS stores local naive datetimes)."""
    return datetime.now(ZoneInfo(settings.HOSPITAL_TIMEZONE)).replace(tzinfo=None, microsecond=0)


def check_event_time(value: datetime, field: str) -> datetime:
    value = value.replace(tzinfo=None, microsecond=0)
    now = now_local()
    if value > now + FUTURE_TOLERANCE:
        raise ValueError(f"{field} cannot be in the future")
    if value < now - timedelta(days=settings.RECORDS_MAX_BACKDATE_DAYS):
        raise ValueError(f"{field} cannot be more than {settings.RECORDS_MAX_BACKDATE_DAYS} days in the past")
    return value


class RecordModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PatientIn(RecordModel):
    document_type: str = Field(pattern=r"^[A-Z]{1,4}$")
    full_name: str = Field(min_length=3, max_length=120)
    birth_date: date
    sex: str = Field(min_length=1, max_length=20)
    insurer: str = Text
    regime: str = Field(min_length=1, max_length=60)
    department: str = Field(min_length=1, max_length=80)
    municipality: str = Field(min_length=1, max_length=80)
    zone: str = Field(min_length=1, max_length=20)

    @field_validator("birth_date")
    @classmethod
    def _birth_date(cls, value: date) -> date:
        if value > now_local().date() or value.year < 1900:
            raise ValueError("birth_date is out of range")
        return value

    def to_his_row(self, patient_id: int) -> dict[str, Any]:
        return {
            "IdPaciente": patient_id, "TipoDocumento": self.document_type, "NombrePaciente": self.full_name,
            "FechaNacimiento": datetime.combine(self.birth_date, datetime.min.time()), "Sexo": self.sex,
            "Asegurador": self.insurer, "Regimen": self.regime, "Departamento": self.department,
            "Municipio": self.municipality, "Zona": self.zone,
        }


class TriageIn(RecordModel):
    triage_date: datetime
    level: int = Field(ge=1, le=5)
    category: Literal["ADULTOS", "PEDIATRIA", "GINECOOBSTETRICIA"] = "ADULTOS"
    chief_complaint: str = Field(min_length=2, max_length=300)
    blood_pressure: Optional[str] = Field(default=None, pattern=r"^\d{2,3}/\d{2,3}$")
    heart_rate: Optional[float] = Field(default=None, ge=20, le=250)
    respiratory_rate: Optional[float] = Field(default=None, ge=4, le=80)
    temperature: Optional[float] = Field(default=None, ge=30, le=45)
    room_code: Optional[int] = Field(default=None, ge=0, le=100_000)

    @field_validator("triage_date")
    @classmethod
    def _when(cls, value: datetime) -> datetime:
        return check_event_time(value, "triage_date")

    @property
    def classification(self) -> str:
        """Same text format as the HIS, so the ETL rule derives the same level from it."""
        return f"{self.category}- TRIAGE {self.level}"

    @model_validator(mode="after")
    def _etl_agrees(self) -> "TriageIn":
        if triage_level(self.classification) != self.level:  # the ETL parser must read the same level
            raise ValueError("triage classification is not parseable by the ETL")
        return self

    def to_his_row(self, triage_id: int, patient_id: int) -> dict[str, Any]:
        return {
            "OidTriage": triage_id, "IdPaciente2": patient_id, "FechaTriage": self.triage_date,
            "MotivoConsulta": self.chief_complaint, "TensionArterial": self.blood_pressure,
            "FrecuenciaCardiaca": self.heart_rate, "FrecuenciaRespiratoria": self.respiratory_rate,
            "Temperatura": self.temperature, "CodigoTriage": self.room_code,
            "ClasificacionTriage": self.classification,
        }


class AdmissionIn(RecordModel):
    patient_id: int = Field(gt=0, lt=10**15)
    patient: Optional[PatientIn] = None  # required only when the patient is new
    admission_class: str = Field(min_length=1, max_length=60)
    admission_route: str = Field(min_length=1, max_length=60)
    risk_type: str = Field(min_length=1, max_length=80)
    admission_date: datetime
    hospitalization_date: Optional[datetime] = None
    attention_date: Optional[datetime] = None
    bed_group: str = Field(min_length=1, max_length=80)
    bed_subgroup: Optional[str] = Field(default=None, max_length=80)
    bed_code: str = Code
    bed_name: str = Field(min_length=1, max_length=80)
    diagnosis_code: str = Field(pattern=r"^[A-Z][0-9]{2}[0-9A-Z]{0,2}$")
    diagnosis_name: str = Field(min_length=2, max_length=200)
    triage: Optional[TriageIn] = None

    @field_validator("admission_date", "hospitalization_date", "attention_date")
    @classmethod
    def _when(cls, value: Optional[datetime], info) -> Optional[datetime]:
        return None if value is None else check_event_time(value, info.field_name)

    @model_validator(mode="after")
    def _chronology(self) -> "AdmissionIn":
        for name in ("hospitalization_date", "attention_date"):
            value = getattr(self, name)
            if value is not None and value < self.admission_date:
                raise ValueError(f"{name} cannot be earlier than admission_date")
        if self.triage and abs(self.triage.triage_date - self.admission_date) > timedelta(hours=24):
            raise ValueError("triage_date must be within 24 hours of admission_date")
        return self

    def ingreso_row(self, admission_id: int, admission_number: int, triage_id: Optional[int]) -> dict[str, Any]:
        return {
            "OidIngreso": admission_id, "ConsecutivoIngreso": admission_number, "IdPaciente": self.patient_id,
            "ClaseIngreso": self.admission_class, "ViaIngreso": self.admission_route, "TipoRiesgo": self.risk_type,
            "FechaIngreso": self.admission_date, "FechaHospitalizacion": self.hospitalization_date,
            "OidTriageA": triage_id, "CodigoCama": self.bed_code, "NombreCama": self.bed_name,
            "NombreGrupoCama": self.bed_group, "NombreSubgrupoCama": self.bed_subgroup or self.bed_group,
            "CodigoDiagnostico": self.diagnosis_code, "NombreDiagnostico": self.diagnosis_name,
        }


class _LineIn(RecordModel):
    admission_id: int = Field(gt=0, lt=10**16)
    code: str = Code
    name: str = Field(min_length=2, max_length=200)
    quantity: int = Field(ge=1, le=10_000)
    service_date: datetime
    area: str = Field(min_length=1, max_length=120)
    specialty: str = Field(min_length=1, max_length=120)

    @field_validator("service_date")
    @classmethod
    def _when(cls, value: datetime) -> datetime:
        return check_event_time(value, "service_date")


class ServiceLineIn(_LineIn):
    """A service provided (HIS sheet `Servicios`). `code` is the CUPS code."""
    area_code: Optional[int] = Field(default=None, ge=0, le=10**9)

    def to_his_row(self, line_id: int) -> dict[str, Any]:
        return {
            "OidIngreso": self.admission_id, "OidS": line_id, "CodigoServicio": self.code,
            "NombreServicio": self.name, "Cantidad": self.quantity, "FechaPrestacion": self.service_date,
            "CodigoAreaServicio": self.area_code, "AreaServicio": self.area, "Especialidad": self.specialty,
        }


class MedicationLineIn(_LineIn):
    """A medication / supply dispensed (HIS sheet `MedicamentoInsumo`)."""

    def to_his_row(self, line_id: int) -> dict[str, Any]:
        return {
            "OidIngreso": self.admission_id, "OidMI": line_id, "CodigoServicio": self.code,
            "NombreServicio": self.name, "Cantidad": self.quantity, "FechaPrestacion": self.service_date,
            "AreaServicio": self.area, "Especialidad": self.specialty,
        }


RecordKind = Literal["admission", "service", "medication"]
MODELS: dict[str, type[RecordModel]] = {
    "admission": AdmissionIn, "service": ServiceLineIn, "medication": MedicationLineIn,
}
