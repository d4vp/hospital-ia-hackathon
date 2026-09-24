"""Systems of record for inserted data.

`RecordStore` is the port; two adapters implement it:

- `SqlServerStore`  the hospital's SQL Server (HIS). Rows are written in ONE transaction per
                    record with parameterised statements (never string formatting of
                    values); ids come from the database (`OUTPUT INSERTED.<id>`), and the
                    foreign keys of the HIS schema guarantee referential integrity.
- `LocalIdStore`    "mongo_only" mode (no SQL Server configured): ids are allocated from an
                    atomic counter in a reserved range (APP_ID_BASE and above) that can never
                    collide with ids exported from the HIS.

pymssql is imported lazily, so the dependency is only needed when SQL Server is enabled.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Optional, Protocol

from pymongo import DESCENDING, ReturnDocument

from app.core.config import COLLECTIONS, settings
from app.services.records.models import AdmissionIn, MedicationLineIn, ServiceLineIn

logger = logging.getLogger("records")

APP_ID_BASE = 9_000_000_000_000  # < 2**53: safe in JSON / JavaScript clients too


class StoreUnavailableError(RuntimeError):
    """The system of record cannot be reached (nothing was written)."""


class StoreIntegrityError(ValueError):
    """The system of record rejected the row (duplicate, missing parent, constraint)."""


@dataclass(frozen=True)
class AdmissionIds:
    admission_id: int
    admission_number: int
    triage_id: Optional[int]


class RecordStore(Protocol):
    name: str

    async def get_patient(self, patient_id: int) -> Optional[dict[str, Any]]: ...

    async def create_admission(self, record: AdmissionIn, new_patient: Optional[dict[str, Any]]) -> AdmissionIds: ...

    async def create_service(self, record: ServiceLineIn) -> int: ...

    async def create_medication(self, record: MedicationLineIn) -> int: ...


# --------------------------------------------------------------------------- #
# MongoDB-only mode
# --------------------------------------------------------------------------- #
PATIENT_FIELDS = {  # Mongo `patient.*` -> HIS column
    "document_type": "TipoDocumento", "name": "NombrePaciente", "birth_date": "FechaNacimiento", "sex": "Sexo",
    "insurer": "Asegurador", "regime": "Regimen", "department": "Departamento", "municipality": "Municipio",
    "zone": "Zona",
}


async def patient_from_mongo(db, patient_id: int) -> Optional[dict[str, Any]]:
    """HIS row of a patient, rebuilt from their latest admission (patient data is embedded)."""
    doc = await db[COLLECTIONS["admissions"]].find_one(
        {"patient.patient_id": patient_id}, {"patient": 1}, sort=[("admission_date", DESCENDING)])
    patient = (doc or {}).get("patient")
    if not patient:
        return None
    return {"IdPaciente": patient_id, **{his: patient.get(field) for field, his in PATIENT_FIELDS.items()}}


class LocalIdStore:
    name = "mongo_only"

    def __init__(self, db) -> None:
        self.db = db

    async def _next(self, sequence: str) -> int:
        doc = await self.db[COLLECTIONS["counters"]].find_one_and_update(
            {"_id": sequence}, {"$inc": {"seq": 1}}, upsert=True, return_document=ReturnDocument.AFTER)
        return APP_ID_BASE + int(doc["seq"])

    async def get_patient(self, patient_id: int) -> Optional[dict[str, Any]]:
        return await patient_from_mongo(self.db, patient_id)

    async def create_admission(self, record: AdmissionIn, new_patient: Optional[dict[str, Any]]) -> AdmissionIds:
        triage_id = await self._next("triage") if record.triage else None
        return AdmissionIds(await self._next("admission"), await self._next("admission_number"), triage_id)

    async def _require_admission(self, admission_id: int) -> None:
        if not await self.db[COLLECTIONS["admissions"]].count_documents({"_id": admission_id}, limit=1):
            raise StoreIntegrityError(f"Admission {admission_id} does not exist")

    async def create_service(self, record: ServiceLineIn) -> int:
        await self._require_admission(record.admission_id)
        return await self._next("service_line")

    async def create_medication(self, record: MedicationLineIn) -> int:
        await self._require_admission(record.admission_id)
        return await self._next("medication_line")


# --------------------------------------------------------------------------- #
# SQL Server
# --------------------------------------------------------------------------- #
def _table(name: str) -> str:
    """Identifiers cannot be parameters: the schema is validated in config, names are constants."""
    return f"[{settings.SQLSERVER_SCHEMA}].[{name}]"


def _insert_sql(table: str, columns: list[str], outputs: tuple[str, ...] = ()) -> str:
    """Parameterised INSERT; `outputs` are returned with OUTPUT INSERTED (identity values)."""
    cols = ", ".join(f"[{c}]" for c in columns)
    marks = ", ".join(["%s"] * len(columns))
    out = (" OUTPUT " + ", ".join(f"INSERTED.[{c}]" for c in outputs)) if outputs else ""
    return f"INSERT INTO {_table(table)} ({cols}){out} VALUES ({marks})"


PATIENT_COLUMNS = ["IdPaciente", *PATIENT_FIELDS.values()]
TRIAGE_COLUMNS = ["IdPaciente2", "FechaTriage", "MotivoConsulta", "TensionArterial", "FrecuenciaCardiaca",
                  "FrecuenciaRespiratoria", "Temperatura", "CodigoTriage", "ClasificacionTriage"]
INGRESO_COLUMNS = ["IdPaciente", "ClaseIngreso", "ViaIngreso", "TipoRiesgo", "FechaIngreso", "FechaHospitalizacion",
                   "OidTriageA", "CodigoCama", "NombreCama", "NombreGrupoCama", "NombreSubgrupoCama",
                   "CodigoDiagnostico", "NombreDiagnostico"]
SERVICE_COLUMNS = ["OidIngreso", "CodigoServicio", "NombreServicio", "Cantidad", "FechaPrestacion",
                   "CodigoAreaServicio", "AreaServicio", "Especialidad"]
MEDICATION_COLUMNS = ["OidIngreso", "CodigoServicio", "NombreServicio", "Cantidad", "FechaPrestacion",
                      "AreaServicio", "Especialidad"]


class SqlServerStore:
    name = "sqlserver"

    def _connect(self):
        import pymssql  # lazy: only required when SQL Server is enabled

        try:
            return pymssql.connect(
                server=settings.SQLSERVER_HOST, port=str(settings.SQLSERVER_PORT), user=settings.SQLSERVER_USER,
                password=settings.SQLSERVER_PASSWORD, database=settings.SQLSERVER_DATABASE,
                login_timeout=settings.SQLSERVER_TIMEOUT_SECONDS, timeout=settings.SQLSERVER_TIMEOUT_SECONDS,
                autocommit=False, tds_version="7.4", appname="hospital-ops-intelligence",
            )
        except pymssql.Error as exc:
            raise StoreUnavailableError("SQL Server is not reachable") from exc

    def _transaction(self, work) -> Any:
        """Runs `work(cursor)` in one transaction: all rows are committed, or none."""
        import pymssql

        conn = self._connect()
        try:
            with conn.cursor() as cursor:
                result = work(cursor)
            conn.commit()
            return result
        except pymssql.IntegrityError as exc:
            conn.rollback()
            raise StoreIntegrityError("SQL Server rejected the record (constraint violation)") from exc
        except pymssql.Error as exc:
            conn.rollback()
            logger.error("sqlserver_write_failed", extra={"data": {"error": type(exc).__name__}})
            raise StoreUnavailableError("SQL Server write failed; nothing was saved") from exc
        finally:
            conn.close()

    async def _run(self, work) -> Any:
        return await asyncio.to_thread(self._transaction, work)

    async def get_patient(self, patient_id: int) -> Optional[dict[str, Any]]:
        def work(cursor) -> Optional[dict[str, Any]]:
            cols = ", ".join(f"[{c}]" for c in PATIENT_COLUMNS)
            cursor.execute(f"SELECT {cols} FROM {_table('Paciente')} WHERE [IdPaciente] = %s", (patient_id,))
            row = cursor.fetchone()
            return dict(zip(PATIENT_COLUMNS, row)) if row else None

        return await self._run(work)

    async def create_admission(self, record: AdmissionIn, new_patient: Optional[dict[str, Any]]) -> AdmissionIds:
        def work(cursor) -> AdmissionIds:
            if new_patient is not None:
                cursor.execute(_insert_sql("Paciente", PATIENT_COLUMNS), tuple(new_patient[c] for c in PATIENT_COLUMNS))
            triage_id: Optional[int] = None
            if record.triage:
                row = record.triage.to_his_row(0, record.patient_id)
                cursor.execute(_insert_sql("Triage", TRIAGE_COLUMNS, ("OidTriage",)),
                               tuple(row[c] for c in TRIAGE_COLUMNS))
                triage_id = int(cursor.fetchone()[0])
            ingreso = record.ingreso_row(0, 0, triage_id)
            cursor.execute(_insert_sql("Ingresos", INGRESO_COLUMNS, ("OidIngreso", "ConsecutivoIngreso")),
                           tuple(ingreso[c] for c in INGRESO_COLUMNS))
            admission_id, admission_number = cursor.fetchone()
            if record.attention_date:
                cursor.execute(_insert_sql("Atencion", ["OidIngreso", "FechaAtencion"]),
                               (admission_id, record.attention_date))
            return AdmissionIds(int(admission_id), int(admission_number), triage_id)

        return await self._run(work)

    async def _create_line(self, table: str, columns: list[str], output: str, row: dict[str, Any]) -> int:
        def work(cursor) -> int:
            cursor.execute(_insert_sql(table, columns, (output,)), tuple(row[c] for c in columns))
            return int(cursor.fetchone()[0])

        return await self._run(work)

    async def create_service(self, record: ServiceLineIn) -> int:
        return await self._create_line("Servicios", SERVICE_COLUMNS, "OidS", record.to_his_row(0))

    async def create_medication(self, record: MedicationLineIn) -> int:
        return await self._create_line("MedicamentoInsumo", MEDICATION_COLUMNS, "OidMI", record.to_his_row(0))


def get_store(db) -> RecordStore:
    return SqlServerStore() if settings.sqlserver_enabled else LocalIdStore(db)
