"""POST /api/auth/login, GET /api/auth/me"""
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, get_db
from app.core.security import create_access_token
from app.services import user_service

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str = Field(max_length=254)
    password: str = Field(max_length=128)


@router.post("/login")
async def login(body: LoginRequest, db=Depends(get_db)) -> dict:
    try:
        user = await user_service.authenticate(db, body.email, body.password)
    except user_service.LoginLockedError as exc:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    token = create_access_token(str(user["_id"]), user["role"])
    return {"access_token": token, "token_type": "bearer", "user": user_service.public_user(user)}


@router.get("/me")
async def me(user: CurrentUser) -> dict:
    return user_service.public_user(user)
