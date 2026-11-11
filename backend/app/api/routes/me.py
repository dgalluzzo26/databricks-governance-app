"""Current-user identity at ``GET /api/me``.

Any signed-in user may call this endpoint.
"""

from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.deps import User
from backend.app.models.user import CurrentUser

router = APIRouter(tags=["identity"])


@router.get("/me", response_model=CurrentUser)
def me(user: User) -> CurrentUser:
    """Return the authenticated caller's profile and roles.

    Any signed-in user.

    Returns:
        The current user, including admin and approver flags.
    """
    return user
