"""Admin-only user management: GET/POST /api/users, PATCH /api/users/{user_id}"""
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import Field

from app.api.deps import AdminUser, get_db
from app.api.models import StrictModel
from app.core.security import PasswordPolicyError
from app.services import user_service

router = APIRouter(prefix="/users", tags=["users"])


class UserCreate(StrictModel):
    email: str = Field(max_length=254)
    full_name: str = Field(default="", max_length=120)
    password: str = Field(min_length=10, max_length=72)
    role: Literal["admin", "user"] = "user"


class UserUpdate(StrictModel):
    full_name: Optional[str] = Field(default=None, max_length=120)
    role: Optional[Literal["admin", "user"]] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(default=None, min_length=10, max_length=72)


@router.get("")
async def list_users(_: AdminUser, db=Depends(get_db)) -> list[dict]:
    return await user_service.list_users(db)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, _: AdminUser, db=Depends(get_db)) -> dict:
    try:
        return await user_service.create_user(db, body.email, body.password, body.full_name, body.role)
    except (user_service.UserError, PasswordPolicyError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.patch("/{user_id}")
async def update_user(user_id: str, body: UserUpdate, admin: AdminUser, db=Depends(get_db)) -> dict:
    try:
        return await user_service.update_user(db, user_id, body.model_dump(exclude_none=True), str(admin["_id"]))
    except user_service.LastAdminError as exc:  # business rule, not a malformed request
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except (user_service.UserError, PasswordPolicyError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
