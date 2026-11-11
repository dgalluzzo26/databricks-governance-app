"""Model describing the signed-in caller."""

from __future__ import annotations

from backend.app.models.base import ApiModel


class CurrentUser(ApiModel):
    """Identity and app roles of the user making the current request.

    Attributes:
        user_name: Databricks user name of the caller.
        email: Email address of the caller.
        display_name: Human-readable name shown in the UI.
        user_id: Databricks user ID, or None when it could not be resolved.
        groups: Display names of the groups the user belongs to.
        is_admin: True when the user is a member of the configured app admin group.
        is_approver: True when the user can decide access requests: always True for admins,
            otherwise derived from the category-approver registry.
        workspace_host: Workspace URL the app is connected to, or None when unknown.
    """

    user_name: str
    email: str
    display_name: str
    user_id: str | None = None
    groups: list[str] = []
    is_admin: bool = False
    is_approver: bool = False
    workspace_host: str | None = None
