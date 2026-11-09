from __future__ import annotations

import logging

from databricks.sdk.errors import DatabricksError

from backend.app.clients.auth import RequestContext
from backend.app.clients.cache import TtlCache
from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.models.user import CurrentUser

logger = logging.getLogger(__name__)


class IdentityService:
    """Resolves the signed-in user and their workspace group memberships."""

    def __init__(self, factory: WorkspaceClientFactory, cache: TtlCache, admin_group: str) -> None:
        self._factory = factory
        self._cache = cache
        self._admin_group = admin_group

    def current_user(self, ctx: RequestContext) -> CurrentUser:
        return self._cache.get_or_load(("user", ctx.user_name.lower()), lambda: self._load(ctx))

    def _load(self, ctx: RequestContext) -> CurrentUser:
        display_name, user_id, groups = ctx.user_name, None, []
        try:
            escaped = ctx.user_name.replace('"', '\\"')
            matches = self._factory.service_principal().users.list(
                filter=f'userName eq "{escaped}"', attributes="id,userName,displayName,groups"
            )
            scim_user = next(iter(matches), None)
            if scim_user is not None:
                display_name = scim_user.display_name or display_name
                user_id = scim_user.id
                groups = sorted({g.display for g in scim_user.groups or [] if g.display})
        except DatabricksError as exc:
            logger.warning("SCIM lookup failed for %s: %s", ctx.user_name, exc)

        return CurrentUser(
            user_name=ctx.user_name,
            email=ctx.email,
            display_name=display_name,
            user_id=user_id,
            groups=groups,
            is_admin=self._admin_group in groups,
            workspace_host=self._factory.host,
        )
