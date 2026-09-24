"""FastAPI application: security middleware, compression, routers, startup tasks and alert scheduler."""
import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from pymongo import ASCENDING, DESCENDING

from app.api.routes import alerts, auth, billing, chat, data, health, integrations, kpis, records, reports, users
from app.core.config import COLLECTIONS, settings
from app.core.logging_config import configure_logging
from app.core.shield import RequestShieldMiddleware
from app.db.mongo import get_async_db
from app.services import alert_service, user_service
from app.services.records import service as records_service
from app.services.data_repository import DatasetNotLoadedError

logger = logging.getLogger("app")


async def _periodic(name: str, interval: int, job) -> None:
    while True:
        try:
            await job(get_async_db())
        except Exception:  # noqa: BLE001 - the scheduler must survive any failure
            logger.exception(f"{name}_failed")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    db = get_async_db()
    if settings.insecure_jwt_secret:
        logger.warning("insecure_jwt_secret: set a random JWT_SECRET of at least 32 characters")
    tasks: list[asyncio.Task] = []
    try:
        await user_service.ensure_indexes(db)
        await db[COLLECTIONS["conversations"]].create_index([("updated_at", ASCENDING)], expireAfterSeconds=24 * 3600)
        await db[COLLECTIONS["agent_logs"]].create_index([("at", DESCENDING)])
        await db[COLLECTIONS["alerts"]].create_index([("status", ASCENDING), ("workflow_status", ASCENDING)])
        await db[COLLECTIONS["alert_history"]].create_index([("closed_at", DESCENDING)])
        await db[COLLECTIONS["alert_events"]].create_index([("at", DESCENDING)])
        await db[COLLECTIONS["alert_events"]].create_index([("alert_key", ASCENDING), ("at", DESCENDING)])
        await db[COLLECTIONS["alert_history"]].create_index([("severity", ASCENDING), ("closed_at", DESCENDING)])
        await user_service.bootstrap_admin(db)
        await db[COLLECTIONS["sync_outbox"]].create_index([("status", ASCENDING), ("created_at", ASCENDING)])
        if settings.ALERT_CHECK_INTERVAL_SECONDS > 0:
            tasks.append(asyncio.create_task(_periodic(
                "alert_loop", settings.ALERT_CHECK_INTERVAL_SECONDS, alert_service.evaluate_and_notify)))
        if settings.SYNC_RETRY_INTERVAL_SECONDS > 0:  # records waiting to reach MongoDB
            tasks.append(asyncio.create_task(_periodic(
                "sync_retry_loop", settings.SYNC_RETRY_INTERVAL_SECONDS, records_service.retry_pending)))
    except Exception:  # noqa: BLE001 - start the API even if Mongo is still booting
        logger.exception("startup_tasks_failed")
    yield
    for task in tasks:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await alert_service.close_http_client()


app = FastAPI(
    title="Hospital Susana López de Valencia — Operations Intelligence API",
    version="2.0.0",
    description="NL2MQL agent, KPIs, alerts and inferential reports. All endpoints except /api/health require a Bearer token.",
    lifespan=lifespan,
)

# KPI, report and billing payloads are large JSON documents: compress them on the wire.
app.add_middleware(GZipMiddleware, minimum_size=1024)

# Bearer tokens travel in the Authorization header, not cookies -> credentials are not needed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH"],  # no PUT / DELETE anywhere in the API
    allow_headers=["Authorization", "Content-Type"],
)


# Outermost layer (added last): hostile requests are rejected before CORS, routing or auth run.
# It also adds the security headers and X-Request-ID to every response.
app.add_middleware(RequestShieldMiddleware)


@app.exception_handler(DatasetNotLoadedError)
async def dataset_not_loaded(_: Request, exc: DatasetNotLoadedError) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc), "code": "dataset_not_loaded"})


ROUTERS = [health.router, auth.router, users.router, data.router, kpis.router, chat.router, alerts.router,
           reports.router, records.router, integrations.router]
if settings.BILLING_ENABLED:  # optional module: absent from the API (and the docs) when disabled
    ROUTERS.append(billing.router)
for router in ROUTERS:
    app.include_router(router, prefix="/api")
