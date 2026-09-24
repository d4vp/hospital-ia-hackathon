"""Platform pieces: data loading endpoints, the real ETL on an .xlsx, app lifecycle, errors,
logging, CLI scripts and the remaining Plan B intents."""
import json
import logging
import runpy

import pandas as pd
import pytest
from mongomock import MongoClient

from app.api.routes import data as data_routes
from app.core.config import COLLECTIONS
from app.core.logging_config import JsonFormatter, configure_logging
from app.db import mongo
from app.db.readonly import ReadOnlyDatabase
from app.services import data_loader, fallback_agent
from tests.fixtures import build_frames

XLSX_BYTES = b"PK\x03\x04" + b"0" * 64
SUMMARY = {"run_id": "r2", "admissions_processed": 7, "reference_date": "2026-09-21T12:00:00"}


# --------------------------------------------------------------------------- #
# Data endpoints
# --------------------------------------------------------------------------- #
@pytest.fixture
def fake_etl(monkeypatch, tmp_path):
    monkeypatch.setattr(data_routes.settings, "DATA_DIR", str(tmp_path))
    calls = []

    def etl():
        calls.append(1)
        return dict(SUMMARY)

    monkeypatch.setattr(data_routes, "load_excel_to_mongo", etl)
    return calls


async def test_load_and_upload(admin_client, user_client, fake_etl, tmp_path):
    assert (await user_client.post("/load-data")).status_code == 403
    loaded = await admin_client.post("/load-data")
    assert loaded.status_code == 200 and loaded.json()["alerts"]["active"] >= 0
    files = {"file": ("DateBaseHIS.xlsx", XLSX_BYTES, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    uploaded = await admin_client.post("/upload-data", files=files)
    assert uploaded.status_code == 200 and (tmp_path / "DateBaseHIS.xlsx").read_bytes() == XLSX_BYTES
    assert len(fake_etl) == 2


@pytest.mark.parametrize("name, content, status", [
    ("data.csv", XLSX_BYTES, 400),        # wrong extension
    ("data.xlsx", b"not a zip file", 400),  # wrong signature
])
async def test_upload_rejections(admin_client, fake_etl, name, content, status):
    response = await admin_client.post("/upload-data", files={"file": (name, content, "application/octet-stream")})
    assert response.status_code == status and not fake_etl


async def test_upload_size_limit(admin_client, fake_etl, monkeypatch):
    monkeypatch.setattr(data_routes.settings, "MAX_UPLOAD_MB", 0)
    response = await admin_client.post("/upload-data", files={"file": ("d.xlsx", XLSX_BYTES, "application/octet-stream")})
    assert response.status_code == 413


@pytest.mark.parametrize("error, status", [(FileNotFoundError("missing"), 404), (ValueError("bad sheets"), 422)])
async def test_etl_errors(admin_client, monkeypatch, error, status):
    def broken():
        raise error

    monkeypatch.setattr(data_routes, "load_excel_to_mongo", broken)
    assert (await admin_client.post("/load-data")).status_code == status


async def test_concurrent_load_is_refused(admin_client, monkeypatch):
    await data_routes._etl_lock.acquire()
    try:
        assert (await admin_client.post("/load-data")).status_code == 409
    finally:
        data_routes._etl_lock.release()


async def test_data_status_and_kpi_routes(user_client):
    status = (await user_client.get("/data/status")).json()
    assert status["loaded"] and status["counts"]["admissions"] == 7
    assert "PEDIATRIA" in (await user_client.get("/filters")).json()["bed_groups"]
    patients = (await user_client.get("/patients", params={"page": 1, "size": 5})).json()
    assert patients["total"] == 7 and len(patients["rows"]) == 5
    report = await user_client.get("/reports/sections")
    assert "trends" in report.json()


async def test_dataset_not_loaded(anon_client, user_client, db):
    await db[COLLECTIONS["metadata"]].delete_many({})
    from app.services.data_repository import invalidate_cache

    invalidate_cache()
    assert (await user_client.get("/data/status")).json() == {"loaded": False}
    response = await user_client.get("/kpis")
    assert response.status_code == 409 and response.json()["code"] == "dataset_not_loaded"


# --------------------------------------------------------------------------- #
# Real ETL on an .xlsx file
# --------------------------------------------------------------------------- #
def _write_workbook(path, frames=None):
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for name, frame in (frames or build_frames()).items():
            frame.to_excel(writer, sheet_name=name, index=False)


def test_etl_end_to_end(tmp_path):
    path = tmp_path / "his.xlsx"
    _write_workbook(path)
    db = MongoClient()["etl"]
    db[COLLECTIONS["admissions"]].insert_one({"_id": 9_000_000_000_001, "etl_run_id": "old",
                                              "record_origin": {"system": "app"}})
    summary = data_loader.load_excel_to_mongo(db=db, path=path)
    assert summary["admissions_processed"] == 7 and summary["app_records_replaced"] == 1
    assert db[COLLECTIONS["admissions"]].count_documents({}) == 7
    assert db[COLLECTIONS["metadata"]].find_one({"_id": "dataset"})["counts"]["admissions"] == 7


def test_etl_file_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        data_loader.load_excel_to_mongo(db=MongoClient()["x"], path=tmp_path / "missing.xlsx")
    path = tmp_path / "partial.xlsx"
    _write_workbook(path, {"Paciente": build_frames()["Paciente"]})
    with pytest.raises(ValueError, match="Missing sheets"):
        data_loader.read_workbook(path)


# --------------------------------------------------------------------------- #
# Lifecycle, database helpers, logging, scripts
# --------------------------------------------------------------------------- #
async def test_lifespan_creates_indexes_and_starts_loops(db, monkeypatch):
    from app import main

    monkeypatch.setattr(main.settings, "BOOTSTRAP_ADMIN_PASSWORD", "Secure/12345")
    monkeypatch.setattr(main.settings, "ALERT_CHECK_INTERVAL_SECONDS", 3600)
    monkeypatch.setattr(main.settings, "SYNC_RETRY_INTERVAL_SECONDS", 3600)
    async with main.app.router.lifespan_context(main.app):
        assert await db[COLLECTIONS["users"]].count_documents({"role": "admin"}) == 1
    indexes = await db[COLLECTIONS["alert_events"]].index_information()
    assert any("at" in str(v["key"]) for v in indexes.values())


async def test_background_job_survives_errors(monkeypatch):
    from app import main

    calls = []

    async def job(_db):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        raise KeyboardInterrupt  # stop the loop on the second run

    monkeypatch.setattr(main.asyncio, "sleep", lambda _s: _noop())
    with pytest.raises(KeyboardInterrupt):
        await main._periodic("test", 1, job)
    assert len(calls) == 2


async def _noop():
    return None


async def test_mongo_helpers(db, monkeypatch):
    assert await mongo.ping() is True  # in-memory database injected by the fixture
    monkeypatch.setattr(mongo, "_db_override", None)
    monkeypatch.setattr(mongo.settings, "MONGO_READONLY_URI", "mongodb://reader:pw@localhost:1/")
    readonly = mongo.get_readonly_db()
    assert isinstance(readonly, ReadOnlyDatabase) and readonly.name == mongo.settings.DB_NAME
    assert mongo.get_readonly_db(readonly) is readonly
    assert mongo.get_sync_db().name == mongo.settings.DB_NAME


async def test_ping_failure(monkeypatch):
    class Broken:
        async def command(self, _):
            raise RuntimeError("down")

    monkeypatch.setattr(mongo, "get_async_db", lambda: Broken())
    assert await mongo.ping() is False


def test_json_logging(capsys):
    configure_logging()
    try:
        raise ValueError("boom")
    except ValueError:
        logging.getLogger("test").exception("failed", extra={"data": {"k": 1}})
    record = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert record["message"] == "failed" and record["k"] == 1 and "ValueError" in record["exception"]
    assert json.loads(JsonFormatter().format(logging.makeLogRecord({"msg": "x"})))["message"] == "x"


async def test_create_admin_script(db, monkeypatch, capsys):
    from app.scripts import create_admin

    monkeypatch.setattr(create_admin.getpass, "getpass", lambda _prompt: "Secure/12345")
    await create_admin._main("cli@h.local", "CLI", "admin")
    assert "Created admin cli@h.local" in capsys.readouterr().out


def test_load_data_script(monkeypatch, capsys):
    monkeypatch.setattr(data_loader, "load_excel_to_mongo", lambda: {"admissions_processed": 7})
    runpy.run_module("app.scripts.load_data", run_name="__main__")
    assert '"admissions_processed": 7' in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Remaining Plan B intents
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("question, intent", [
    ("¿Cuáles son los medicamentos más consumidos?", "top_meds"),
    ("medicamentos de menor rotación", "least_meds"),
    ("cirugías realizadas vs programadas", "surgeries"),
    ("espera por nivel de triage", "wait_by_triage"),
    ("demanda por especialidad el mes pasado", "specialty_demand"),
    ("¿Qué servicio tiene más pacientes ingresados?", "top_service"),
])
async def test_plan_b_intents(db, question, intent):
    metadata = await db[COLLECTIONS["metadata"]].find_one({"_id": "dataset"})
    for lang in ("es", "en"):
        result = await fallback_agent.answer(db, question, lang, metadata)
        assert result.intent == intent and result.answer


async def test_plan_b_no_match(db):
    metadata = await db[COLLECTIONS["metadata"]].find_one({"_id": "dataset"})
    result = await fallback_agent.answer(db, "¿qué tiempo hace?", "es", metadata)
    assert result.intent is None and "contingencia" in result.answer
