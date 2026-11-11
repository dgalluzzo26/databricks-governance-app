"""Signed-in user resolution with workspace group memberships.

Turns the request context (forwarded email and optional user token) into a ``CurrentUser``
with display name, user id, groups, and the app-admin flag, which drive authorization and
group-based attribute inheritance throughout the app. Results are cached per user and token.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

from databricks.sdk.errors import DatabricksError

from backend.app.clients.auth import RequestContext
from backend.app.clients.cache import TtlCache
from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.errors import InvalidRequestError, UpstreamError
from backend.app.models.directory import DirectoryPrincipal, DirectoryPrincipalList
from backend.app.models.principal import PrincipalKind
from backend.app.models.user import CurrentUser

logger = logging.getLogger(__name__)

_SEARCH_LIMIT_DEFAULT = 20
_SEARCH_LIMIT_MAX = 50


@dataclass
class _DirectoryEntry:
    """Directory fields for a user as read from /Me or SCIM.

    Attributes:
        user_id: Workspace user id, if found.
        display_name: Directory display name, if found.
        groups: Display names of the user's workspace groups.
    """

    user_id: str | None = None
    display_name: str | None = None
    groups: list[str] = field(default_factory=list)


class IdentityService:
    """Resolves the signed-in user and their workspace group memberships.

    With a forwarded user token (Databricks Apps user authorization, scope
    iam.current-user:read) the user's own /Me record is read, which includes their groups.
    Without one (local development) the service principal looks the user up through SCIM; a
    non-admin service principal cannot see other users' groups.

    Args:
        factory: Workspace client factory for user-token and service principal clients.
        cache: TTL cache for resolved users.
        admin_group: Workspace group whose members are app admins.
    """

    def __init__(self, factory: WorkspaceClientFactory, cache: TtlCache, admin_group: str) -> None:
        self._factory = factory
        self._cache = cache
        self._admin_group = admin_group

    def search_principals(
        self,
        q: str,
        *,
        kind: PrincipalKind | None = None,
        limit: int = _SEARCH_LIMIT_DEFAULT,
    ) -> DirectoryPrincipalList:
        """Search workspace users and/or groups by name substring.

        Uses the Account Admin SP (falls back to the app SP). SCIM ``co`` (contains)
        filters are applied separately to users and groups, then merged.

        Args:
            q: Search text (at least 2 characters after strip).
            kind: When set, only search that principal kind.
            limit: Maximum results to return (capped at 50).

        Returns:
            Matching principals, truncated when more may exist.

        Raises:
            InvalidRequestError: If ``q`` is too short or ``kind`` is a service principal.
            UpstreamError: If the directory API fails.
        """
        needle = q.strip()
        if len(needle) < 2:
            raise InvalidRequestError("Search query must be at least 2 characters")
        if kind == PrincipalKind.SERVICE_PRINCIPAL:
            raise InvalidRequestError("Service principal search is not supported")
        cap = max(1, min(limit, _SEARCH_LIMIT_MAX))
        escaped = needle.replace("\\", "\\\\").replace('"', '\\"')
        results: list[DirectoryPrincipal] = []
        truncated = False
        try:
            client = self._factory.admin()
            if kind in (None, PrincipalKind.USER):
                users, user_trunc = self._search_users(client, escaped, cap)
                results.extend(users)
                truncated = truncated or user_trunc
            if kind in (None, PrincipalKind.GROUP) and len(results) < cap:
                groups, group_trunc = self._search_groups(client, escaped, cap - len(results))
                results.extend(groups)
                truncated = truncated or group_trunc
        except DatabricksError as exc:
            raise UpstreamError(f"Directory search failed: {exc}") from exc
        return DirectoryPrincipalList(principals=results[:cap], total=len(results[:cap]), truncated=truncated)

    def _search_users(
        self, client: object, escaped: str, limit: int
    ) -> tuple[list[DirectoryPrincipal], bool]:
        """Return up to ``limit`` users matching the escaped SCIM contains filter."""
        listed = client.users.list(  # type: ignore[attr-defined]
            filter=f'userName co "{escaped}" or displayName co "{escaped}"',
            attributes="id,userName,displayName,emails",
            count=limit + 1,
        )
        items: list[DirectoryPrincipal] = []
        for user in listed:
            if len(items) >= limit:
                return items, True
            name = (getattr(user, "user_name", None) or "").strip()
            if not name:
                continue
            display = (getattr(user, "display_name", None) or name).strip()
            email = None
            emails = getattr(user, "emails", None) or []
            for entry in emails:
                value = getattr(entry, "value", None)
                if value:
                    email = str(value)
                    break
            items.append(
                DirectoryPrincipal(
                    name=name,
                    display_name=display,
                    principal_type=PrincipalKind.USER,
                    email=email,
                )
            )
        return items, False

    def _search_groups(
        self, client: object, escaped: str, limit: int
    ) -> tuple[list[DirectoryPrincipal], bool]:
        """Return up to ``limit`` groups matching the escaped SCIM contains filter."""
        listed = client.groups.list(  # type: ignore[attr-defined]
            filter=f'displayName co "{escaped}"',
            attributes="id,displayName",
            count=limit + 1,
        )
        items: list[DirectoryPrincipal] = []
        for group in listed:
            if len(items) >= limit:
                return items, True
            name = (getattr(group, "display_name", None) or "").strip()
            if not name:
                continue
            items.append(
                DirectoryPrincipal(
                    name=name,
                    display_name=name,
                    principal_type=PrincipalKind.GROUP,
                    email=None,
                )
            )
        return items, False

    def current_user(self, ctx: RequestContext) -> CurrentUser:
        """Resolve the caller to a ``CurrentUser``, using the cache when possible.

        Directory lookup failures are logged and degrade to a user with no groups (and so
        no admin rights) rather than raising.

        Args:
            ctx: Request identity built from the Databricks Apps proxy headers.

        Returns:
            The user with groups and ``is_admin`` set from membership in the admin group.
        """
        # Key on the token too, so a new grant of scopes is picked up on the next session.
        token_key = hashlib.sha256(ctx.access_token.encode()).hexdigest()[:16] if ctx.access_token else None
        return self._cache.get_or_load(("user", ctx.user_name.lower(), token_key), lambda: self._load(ctx))

    def _load(self, ctx: RequestContext) -> CurrentUser:
        """Build the user from /Me (user token) or, failing that, a SCIM lookup."""
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
        """Read the caller's /Me record with their token; None if the call fails."""
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
        """Look up the caller by userName via SCIM as the service principal; empty entry if not found."""
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
