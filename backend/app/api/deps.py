"""Shared FastAPI dependencies: database, current user, admin guard, language."""
from typing import Annotated, Optional

import jwt
from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import settings
from app.core.i18n import lang_or_default
from app.core.security import decode_access_token
from app.db.mongo import get_async_db
from app.services import user_service

bearer = HTTPBearer(auto_error=False)


def get_db():
    return get_async_db()


async def get_current_user(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(bearer)],
    db=Depends(get_db),
) -> dict:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError:
        raise unauthorized
    try:
        user = await user_service.get_user(db, payload.get("sub", ""))
    except user_service.UserError:
        raise unauthorized
    if not user or not user.get("is_active", True):
        raise unauthorized
    return user


async def require_admin(user: Annotated[dict, Depends(get_current_user)]) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Administrator role required")
    return user


def get_language(lang: Annotated[Optional[str], Query(description="es | en")] = None) -> str:
    return lang_or_default(lang, settings.DEFAULT_LANGUAGE)


CurrentUser = Annotated[dict, Depends(get_current_user)]
AdminUser = Annotated[dict, Depends(require_admin)]
Language = Annotated[str, Depends(get_language)]
