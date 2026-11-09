from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.deps import User
from backend.app.models.user import CurrentUser

router = APIRouter(tags=["identity"])


@router.get("/me", response_model=CurrentUser)
def me(user: User) -> CurrentUser:
    return user
