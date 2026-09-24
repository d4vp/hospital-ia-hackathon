"""Hybrid record insertion: SQL Server (system of record) -> MongoDB projection, outbox, API."""
import sys
import types
from datetime import timedelta

import pytest
from pymongo.errors import PyMongoError

from app.core.config import COLLECTIONS
from app.services.records import projection, service
from app.services.records.models import AdmissionIn, MedicationLineIn, ServiceLineIn, TriageIn, now_local
from app.services.records.stores import (
    APP_ID_BASE, LocalIdStore, SqlServerStore, StoreIntegrityError, StoreUnavailableError,
)
from app.services.records.service import RecordValidationError

NEW_PATIENT = {"document_type": "CC", "full_name": "Paciente Prueba", "birth_date": "1980-05-01", "sex": "Femenino",
               "insurer": "EPS X", "regime": "Subsidiado", "department": "CAUCA", "municipality": "POPAYÁN",
               "zone": "Urbana"}


def admission(**overrides) -> AdmissionIn:
    now = now_local()
    data = {"patient_id": 4, "admission_class": "Hospitalario", "admission_route": "Urgencias",
            "risk_type": "Enfermedad General", "admission_date": now - timedelta(hours=2),
            "attention_date": now - timedelta(hours=1), "bed_group": "PEDIATRIA", "bed_code": "PED-9",
            "bed_name": "CAMA PED-9", "diagnosis_code": "J189", "diagnosis_name": "NEUMONIA",
            "triage": TriageIn(triage_date=now - timedelta(hours=2), level=2, category="PEDIATRIA",
                               chief_complaint="fiebre")}
    return AdmissionIn(**{**data, **overrides})


def line(model, admission_id, **overrides):
    data = {"admission_id": admission_id, "code": "M1", "name": "AMOXICILINA 500 MG", "quantity": 2,
            "service_date": now_local() - timedelta(minutes=30), "area": "PEDIATRIA", "specialty": "PEDIATRIA"}
    return model(**{**data, **overrides})


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("overrides", [
    {"admission_date": now_local() + timedelta(days=1)},
    {"admission_date": now_local() - timedelta(days=400)},
    {"attention_date": now_local() - timedelta(days=2)},  # before the admission
    {"diagnosis_code": "neumonia"},
    {"bed_code": "'; DROP TABLE Ingresos;--"},
])
def test_invalid_admissions_are_rejected(overrides):
    with pytest.raises(ValueError):
        admission(**overrides)


def test_extra_fields_are_rejected():
    with pytest.raises(ValueError):
        AdmissionIn(**{**admission().model_dump(), "etl_run_id": "x"})


# --------------------------------------------------------------------------- #
# mongo_only mode
# --------------------------------------------------------------------------- #
async def test_admission_is_projected_with_the_etl_structure(db, bundle):
    result = await service.create_admission(db, admission(), "boss@h.local")
    assert result["status"] == "synced" and result["store"] == "mongo_only"
    assert result["admission_id"] > APP_ID_BASE
    doc = await db[COLLECTIONS["admissions"]].find_one({"_id": result["admission_id"]})
    etl_doc = bundle.admissions[0]
    assert set(doc) - {"record_origin"} == set(etl_doc)  # same shape as a workbook-loaded admission
    assert doc["triage"]["level"] == 2 and doc["diagnosis"]["chapter"] == "respiratory"
    assert doc["wait_minutes"] == 60.0 and doc["patient"]["municipality"] == "POPAYÁN"  # existing patient reused
    assert doc["record_origin"]["created_by"] == "boss@h.local" and doc["currently_admitted"]
    metadata = await db[COLLECTIONS["metadata"]].find_one({"_id": "dataset"})
    assert metadata["counts"]["admissions"] == 8 and metadata["revision"] == 1
    assert metadata["reference_date"] == doc["admission_date"]  # "now" moved forward


async def test_new_patient_requires_patient_data(db):
    with pytest.raises(RecordValidationError, match="provide the patient data"):
        await service.create_admission(db, admission(patient_id=777), "boss")
    result = await service.create_admission(db, admission(patient_id=777, patient=NEW_PATIENT), "boss")
    doc = await db[COLLECTIONS["admissions"]].find_one({"_id": result["admission_id"]})
    assert doc["patient"]["name"] == "Paciente Prueba" and doc["patient"]["age_group"]


@pytest.mark.parametrize("overrides, message", [
    ({"bed_group": "MARTE"}, "bed group"),
    ({"admission_class": "Teletransporte"}, "admission_class"),
    ({"patient_id": 778, "patient": {**NEW_PATIENT, "regime": "Inventado"}}, "regime"),
])
async def test_catalog_validation(db, overrides, message):
    with pytest.raises(RecordValidationError, match=message):
        await service.create_admission(db, admission(**overrides), "boss")


async def test_lines_update_the_admission_and_daily_aggregates(db):
    created = await service.create_admission(db, admission(), "boss")
    adm_id = created["admission_id"]
    med = await service.create_medication(db, line(MedicationLineIn, adm_id), "boss")
    srv = await service.create_service(db, line(ServiceLineIn, adm_id, code="890201", name="CONSULTA",
                                                  quantity=1), "boss")
    assert med["status"] == srv["status"] == "synced"
    doc = await db[COLLECTIONS["admissions"]].find_one({"_id": adm_id})
    assert [m["code"] for m in doc["medications"]] == ["M1"]
    assert doc["services"][0]["cups_code"] == "890201"  # same field name as ETL-loaded service lines
    assert doc["primary_specialty"] == "PEDIATRIA" and doc["record_revision"] == 2
    usage = await db[COLLECTIONS["medication_usage_daily"]].find_one({"code": "M1", "lines": 1, "quantity": 2})
    demand = await db[COLLECTIONS["service_demand_daily"]].find_one({"specialty": "PEDIATRIA", "area": "PEDIATRIA"})
    assert usage and demand["admissions"] == 1
    # Re-projecting the same line is a no-op (idempotent retries).
    assert await projection.project_line(db, "medication", line(MedicationLineIn, adm_id), med["line_id"]) is False


async def test_service_marks_scheduled_surgery_as_performed(db):
    before = await db[COLLECTIONS["admissions"]].find_one({"_id": 103})
    assert before["surgeries_performed"] == 1
    admissions = db[COLLECTIONS["admissions"]]
    await admissions.update_one({"_id": 103}, {"$set": {"admission_date": now_local() - timedelta(days=1)}})
    await service.create_service(db, line(ServiceLineIn, 103, code="999999", name="CIRUGIA"), "boss")
    after = await admissions.find_one({"_id": 103})
    assert after["surgeries_performed"] == 2 and all(s["performed"] for s in after["scheduled_surgeries"])


async def test_line_rules(db):
    with pytest.raises(StoreIntegrityError):
        await service.create_service(db, line(ServiceLineIn, 424242), "boss")
    created = await service.create_admission(db, admission(), "boss")
    early = now_local() - timedelta(days=1)
    with pytest.raises(RecordValidationError, match="earlier"):
        await service.create_medication(db, line(MedicationLineIn, created["admission_id"], service_date=early), "boss")


# --------------------------------------------------------------------------- #
# Outbox
# --------------------------------------------------------------------------- #
async def test_failed_projection_is_queued_and_retried(db, monkeypatch):
    original = projection.project_admission

    async def broken(*args, **kwargs):
        raise PyMongoError("mongo down")

    monkeypatch.setattr(projection, "project_admission", broken)
    result = await service.create_admission(db, admission(), "boss")
    assert result["status"] == "pending_sync" and result["outbox_id"]
    assert await db[COLLECTIONS["admissions"]].count_documents({"_id": result["admission_id"]}) == 0
    assert (await service.retry_pending(db)) == {"done": 0, "pending": 1, "failed": 0}

    monkeypatch.setattr(projection, "project_admission", original)
    assert (await service.retry_pending(db))["done"] == 1
    assert await db[COLLECTIONS["admissions"]].count_documents({"_id": result["admission_id"]}) == 1
    status = await service.sync_status(db)
    assert status["counts"] == {"pending": 0, "failed": 0, "done": 1}


async def test_outbox_gives_up_after_max_attempts(db, monkeypatch):
    async def broken(*args, **kwargs):
        raise PyMongoError("still down")

    monkeypatch.setattr(projection, "project_line", broken)
    created = await service.create_admission(db, admission(), "boss")
    queued = await service.create_medication(db, line(MedicationLineIn, created["admission_id"]), "boss")
    await db[COLLECTIONS["sync_outbox"]].update_one({"_id": queued["outbox_id"]},
                                                    {"$set": {"attempts": service.MAX_SYNC_ATTEMPTS - 1}})
    assert (await service.retry_pending(db))["failed"] == 1


# --------------------------------------------------------------------------- #
# SQL Server adapter (fake driver: records every statement)
# --------------------------------------------------------------------------- #
class FakeDriver:
    class Error(Exception):
        pass

    class IntegrityError(Error):
        pass

    def __init__(self, results=(), fail_with=None, refuse_connection=False):
        self.results = list(results)
        self.fail_with = fail_with
        self.refuse_connection = refuse_connection
        self.statements: list[tuple[str, tuple]] = []
        self.committed = self.rolled_back = self.closed = 0
        self.connect_kwargs: dict = {}

    def connect(self, **kwargs):
        if self.refuse_connection:
            raise self.Error("no route to host")
        self.connect_kwargs = kwargs
        driver = self

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params):
                driver.statements.append((sql, params))
                if driver.fail_with:
                    raise driver.fail_with

            def fetchone(self):
                return driver.results.pop(0) if driver.results else None

        class Conn:
            def cursor(self):
                return Cursor()

            def commit(self):
                driver.committed += 1

            def rollback(self):
                driver.rolled_back += 1

            def close(self):
                driver.closed += 1

        return Conn()


@pytest.fixture
def fake_sqlserver(monkeypatch):
    def install(**kwargs):
        driver = FakeDriver(**kwargs)
        module = types.ModuleType("pymssql")
        module.connect, module.Error, module.IntegrityError = driver.connect, FakeDriver.Error, FakeDriver.IntegrityError
        monkeypatch.setitem(sys.modules, "pymssql", module)
        monkeypatch.setattr(service.settings, "SQLSERVER_HOST", "sql.hospital.local")
        return driver
    return install


async def test_sqlserver_admission_is_one_parameterised_transaction(db, fake_sqlserver):
    driver = fake_sqlserver(results=[None, (555,), (1001, 77)])  # patient lookup, triage id, admission ids
    record = admission(patient_id=779, patient=NEW_PATIENT)
    result = await service.create_admission(db, record, "boss")
    assert result["store"] == "sqlserver" and result["admission_id"] == 1001 and result["triage_id"] == 555
    tables = [sql.split("[dbo].")[1].split(" ")[0] for sql, _ in driver.statements]
    assert tables == ["[Paciente]", "[Paciente]", "[Triage]", "[Ingresos]", "[Atencion]"]
    for sql, params in driver.statements:
        assert "%s" in sql and "Paciente Prueba" not in sql  # values only travel as parameters
    assert driver.statements[1][1][2] == "Paciente Prueba"
    assert driver.committed == 2 and driver.rolled_back == 0 and driver.closed == 2
    assert driver.connect_kwargs["autocommit"] is False
    doc = await db[COLLECTIONS["admissions"]].find_one({"_id": 1001})
    assert doc["admission_number"] == 77 and doc["triage"]["triage_id"] == 555


async def test_sqlserver_errors_roll_back(db, fake_sqlserver):
    store = SqlServerStore()
    driver = fake_sqlserver(fail_with=FakeDriver.IntegrityError("FK violation"))
    with pytest.raises(StoreIntegrityError):
        await store.create_service(line(ServiceLineIn, 1))
    assert driver.rolled_back == 1 and driver.committed == 0
    driver = fake_sqlserver(fail_with=FakeDriver.Error("deadlock"))
    with pytest.raises(StoreUnavailableError):
        await store.create_medication(line(MedicationLineIn, 1))
    assert driver.rolled_back == 1
    fake_sqlserver(refuse_connection=True)
    with pytest.raises(StoreUnavailableError):
        await store.get_patient(1)


async def test_sqlserver_lines_and_patient_lookup(db, fake_sqlserver):
    fake_sqlserver(results=[(5,), (6,), (4, "CC", "X", None, "Femenino", "EPS X", "Subsidiado", "C", "P", "Urbana")])
    store = SqlServerStore()
    assert await store.create_service(line(ServiceLineIn, 101)) == 5
    assert await store.create_medication(line(MedicationLineIn, 101)) == 6
    assert (await store.get_patient(4))["Regimen"] == "Subsidiado"


async def test_local_store_allocates_ids_in_reserved_range(db):
    store = LocalIdStore(db)
    ids = await store.create_admission(admission(triage=None), None)
    assert ids.triage_id is None and ids.admission_id == APP_ID_BASE + 1
    assert (await store.create_admission(admission(triage=None), None)).admission_id == APP_ID_BASE + 2


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
async def test_records_api(admin_client, user_client):
    body = admission().model_dump(mode="json")
    assert (await user_client.post("/records/admissions", json=body)).status_code == 403
    catalog = (await admin_client.get("/records/catalog")).json()
    assert catalog["mode"] == "mongo_only" and "PEDIATRIA" in catalog["bed_groups"]

    before = (await admin_client.get("/kpis")).json()["cards"]["admissions"]
    created = await admin_client.post("/records/admissions", json=body)
    assert created.status_code == 201 and created.json()["status"] == "synced"
    after = (await admin_client.get("/kpis")).json()["cards"]["admissions"]
    assert after == before + 1  # caches follow the dataset revision

    med = line(MedicationLineIn, created.json()["admission_id"]).model_dump(mode="json")
    assert (await admin_client.post("/records/medications", json=med)).status_code == 201
    srv = line(ServiceLineIn, created.json()["admission_id"], code="890201").model_dump(mode="json")
    assert (await admin_client.post("/records/services", json=srv)).status_code == 201

    bad = {**body, "bed_group": "MARTE"}
    assert (await admin_client.post("/records/admissions", json=bad)).status_code == 422
    missing_parent = {**med, "admission_id": 123456}
    assert (await admin_client.post("/records/medications", json=missing_parent)).status_code == 409
    injected = {**body, "extra": {"$set": {"role": "admin"}}}
    assert (await admin_client.post("/records/admissions", json=injected)).status_code == 400
    assert (await admin_client.get("/records/sync")).json()["counts"]["pending"] == 0
    assert (await admin_client.post("/records/sync/retry")).json() == {"done": 0, "pending": 0, "failed": 0}


async def test_records_api_reports_sqlserver_outage(admin_client, fake_sqlserver):
    fake_sqlserver(refuse_connection=True)
    response = await admin_client.post("/records/admissions", json=admission().model_dump(mode="json"))
    assert response.status_code == 503
