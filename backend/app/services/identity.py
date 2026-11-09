from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

from databricks.sdk.errors import DatabricksError

from backend.app.clients.auth import RequestContext
from backend.app.clients.cache import TtlCache
from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.models.user import CurrentUser

logger = logging.getLogger(__name__)


@dataclass
class _DirectoryEntry:
    user_id: str | None = None
    display_name: str | None = None
    groups: list[str] = field(default_factory=list)


class IdentityService:
    """Resolves the signed-in user and their workspace group memberships.

    With a forwarded user token (Databricks Apps user authorization, scope
    iam.current-user:read) the user's own /Me record is read, which includes their groups.
    Without one (local development) the service principal looks the user up through SCIM; a
    non-admin service principal cannot see other users' groups.
    """

    def __init__(self, factory: WorkspaceClientFactory, cache: TtlCache, admin_group: str) -> None:
        self._factory = factory
        self._cache = cache
        self._admin_group = admin_group

    def current_user(self, ctx: RequestContext) -> CurrentUser:
        # Key on the token too, so a new grant of scopes is picked up on the next session.
        token_key = hashlib.sha256(ctx.access_token.encode()).hexdigest()[:16] if ctx.access_token else None
        return self._cache.get_or_load(("user", ctx.user_name.lower(), token_key), lambda: self._load(ctx))

    def _load(self, ctx: RequestContext) -> CurrentUser:
        entry = self._from_user_token(ctx) if ctx.access_token else None
        if entry is None:
            entry = self._from_scim(ctx)
        groups = sorted(set(entry.groups))
        return CurrentUser(
            user_name=ctx.user_name,
            email=ctx.email,
            display_name=entry.display_name or ctx.display_name or ctx.user_name,
            user_id=entry.user_id,
            groups=groups,
            is_admin=self._admin_group in groups,
            workspace_host=self._factory.host,
        )

    def _from_user_token(self, ctx: RequestContext) -> _DirectoryEntry | None:
        try:
            me = self._factory.for_user(ctx.access_token or "").current_user.me()
        except DatabricksError as exc:
            logger.warning("Reading /Me with the user token failed for %s: %s", ctx.user_name, exc)
            return None
        return _DirectoryEntry(
            user_id=me.id,
            display_name=me.display_name,
            groups=[g.display for g in me.groups or [] if g.display],
        )

    def _from_scim(self, ctx: RequestContext) -> _DirectoryEntry:
        try:
            escaped = ctx.user_name.replace('"', '\\"')
            matches = self._factory.service_principal().users.list(
                filter=f'userName eq "{escaped}"', attributes="id,userName,displayName,groups"
            )
            scim_user = next(iter(matches), None)
        except DatabricksError as exc:
            logger.warning("SCIM lookup failed for %s: %s", ctx.user_name, exc)
            return _DirectoryEntry()
        if scim_user is None:
            return _DirectoryEntry()
        return _DirectoryEntry(
            user_id=scim_user.id,
            display_name=scim_user.display_name,
            groups=[g.display for g in scim_user.groups or [] if g.display],
        )
