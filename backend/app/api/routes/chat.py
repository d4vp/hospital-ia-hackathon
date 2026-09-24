"""POST /api/chat and its alias POST /api/query (name used by the challenge)."""
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, get_db
from app.core.config import settings
from app.core.i18n import lang_or_default
from app.services import chat_service

router = APIRouter(tags=["agent"])


class ChatRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    conversation_id: Optional[str] = Field(default=None, max_length=64, pattern=r"^[a-f0-9]{8,64}$")
    language: Optional[str] = Field(default=None, pattern=r"^(es|en)$")


async def _handle(body: ChatRequest, user: dict, db) -> dict:
    lang = lang_or_default(body.language, settings.DEFAULT_LANGUAGE)
    return await chat_service.ask(db, user, body.question.strip(), body.conversation_id, lang)


@router.post("/chat")
async def chat(body: ChatRequest, user: CurrentUser, db=Depends(get_db)) -> dict:
    return await _handle(body, user, db)


@router.post("/query")
async def query(body: ChatRequest, user: CurrentUser, db=Depends(get_db)) -> dict:
    """Alias of /api/chat."""
    return await _handle(body, user, db)
