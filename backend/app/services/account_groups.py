"""Account group memberships for attribute inheritance, including nested groups.

Workspace ``/Me`` lists only groups assigned to this workspace. Account SCIM
``Users/{id}?attributes=groups`` returns the user's account groups with nesting already
expanded (a direct ``dac_finance`` member also sees ``dac_finance_budget``). That single
lookup is used instead of paging every account group with its members, which is too slow
for interactive ``/api/me`` calls.
"""

from __future__ import annotations

import logging

from databricks.sdk.errors import DatabricksError

from backend.app.clients.cache import TtlCache
from backend.app.clients.workspace import WorkspaceClientFactory

logger = logging.getLogger(__name__)

_ACCOUNT_SCIM_USER = "/api/2.0/account/scim/v2/Users/{user_id}"


def group_names_from_user_resource(resource: dict) -> list[str]:
    """Display names from an Account SCIM User ``groups`` attribute.

    Args:
        resource: User resource as returned by Account SCIM.

    Returns:
        Sorted, de-duplicated group display names.
    """
    names = {
        str(group.get("display") or "").strip()
        for group in (resource.get("groups") or [])
        if group.get("display")
    }
    return sorted({name for name in names if name}, key=str.lower)


class AccountGroupResolver:
    """Resolves account group memberships for a user, including nested groups.

    Each user is read once per cache TTL via Account SCIM. When Account SCIM is unavailable
    the resolver returns no extra groups and the caller falls back to workspace ``/Me``.

    Args:
        factory: Workspace client factory; the Account Admin SP reads Account SCIM.
        cache: TTL cache shared with identity lookups.
    """

    def __init__(self, factory: WorkspaceClientFactory, cache: TtlCache) -> None:
        self._factory = factory
        self._cache = cache

    def groups_for(self, user_id: str | None) -> list[str]:
        """Account groups the user belongs to, directly or through nesting.

        Args:
            user_id: Account SCIM id of the user, or None when unknown.

        Returns:
            Group display names; empty when Account SCIM is not configured or cannot be read.
        """
        if not user_id or not self._factory.admin_configured:
            return []
        try:
            return self._cache.get_or_load(
                ("account-user-groups", user_id),
                lambda: self._load_user_groups(user_id),
            )
        except DatabricksError as exc:
            logger.warning(
                "Reading account group memberships failed; using workspace groups: %s", exc
            )
            return []

    def _load_user_groups(self, user_id: str) -> list[str]:
        """Read one Account SCIM user and return expanded group display names."""
        admin = self._factory.admin()
        payload = admin.api_client.do(
            "GET",
            _ACCOUNT_SCIM_USER.format(user_id=user_id),
            query={"attributes": "id,groups"},
        )
        resource = payload if isinstance(payload, dict) else {}
        return group_names_from_user_resource(resource)
