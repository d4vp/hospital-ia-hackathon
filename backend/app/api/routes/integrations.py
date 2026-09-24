"""Read-only API for the n8n AI agent (tools of the "AI Agent" node in docs/n8n-workflow.json).

Security model
- Authenticated ONLY by `X-Integration-Key` (INTEGRATION_API_KEY, >= 32 chars, compared in
  constant time). The key is useless anywhere else: every other endpoint requires a user JWT,
  and these endpoints do not accept a JWT. Disabled (404) while the key is not configured.
- There is no write endpoint here, and the agent never sees a query language: it sends a
  natural-language question, which goes through the same pipeline as the chat
  (input screen -> query guard whitelists -> read-only DB facade -> PII removal).
- Rate-limited by the request shield (RATE_LIMIT_AGENT_PER_MIN per key).

POST /api/integrations/n8n/ask                 {question, language} -> answer (+ small table)
GET  /api/integrations/n8n/alerts/summary      pending / in-progress counters and one line per alert
GET  /api/integrations/n8n/alerts/{key}        one active alert with its figures and status history
"""
import hmac
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import Field

from app.api.deps import get_db
from app.api.models import StrictModel
from app.core.config import settings
from app.services import alert_service, chat_service

router = APIRouter(prefix="/integrations/n8n", tags=["integrations"])

INTEGRATION_IDENTITY = {"_id": "integration:n8n", "role": "integration", "email": "n8n"}
MAX_TABLE_ROWS = 20


async def require_integration(
    x_integration_key: Annotated[Optional[str], Header(max_length=256)] = None,
) -> dict:
    if not settings.integration_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not x_integration_key or not hmac.compare_digest(x_integration_key.encode(), settings.INTEGRATION_API_KEY.encode()):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid integration key")
    return INTEGRATION_IDENTITY


Integration = Annotated[dict, Depends(require_integration)]
Lang = Annotated[Literal["es", "en"], Query()]


class AskRequest(StrictModel):
    question: str = Field(min_length=2, max_length=1000)
    language: Literal["es", "en"] = "es"


@router.post("/ask")
async def ask(body: AskRequest, identity: Integration, db=Depends(get_db)) -> dict:
    result = await chat_service.ask(db, identity, body.question, None, body.language)
    table = result.get("table")
    if table:
        table = {"columns": table["columns"], "rows": table["rows"][:MAX_TABLE_ROWS]}
    return {"answer": result["answer"], "table": table, "engine": result["engine"],
            "reference_date": result["reference_date"], "read_only": True}


@router.get("/alerts/summary")
async def alerts_summary(_: Integration, lang: Lang = "es", db=Depends(get_db)) -> dict:
    return await alert_service.alerts_summary(db, lang)


@router.get("/alerts/{key:path}")
async def alert_detail(key: str, _: Integration, lang: Lang = "es", db=Depends(get_db)) -> dict:
    alert = next((a for a in await alert_service.active_alerts(db, lang) if a["key"] == key), None)
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not active")
    return alert
