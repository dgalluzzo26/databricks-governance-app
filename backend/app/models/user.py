from __future__ import annotations

from backend.app.models.base import ApiModel


class CurrentUser(ApiModel):
    user_name: str
    email: str
    display_name: str
    user_id: str | None = None
    groups: list[str] = []
    is_admin: bool = False
    is_approver: bool = False
    workspace_host: str | None = None
