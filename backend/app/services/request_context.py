"""Caller identity extraction from Databricks Apps proxy headers.

Produces the ``RequestContext`` (user name, email, display name, optional forwarded user
token) that identity resolution and on-behalf-of-user calls build on, with a local
development fallback when no proxy headers are present.
"""

from __future__ import annotations

from collections.abc import Mapping

from backend.app.clients.auth import RequestContext
from backend.app.clients.cache import TtlCache
from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.config import AppConfig
from backend.app.core.errors import UnauthorizedError


class RequestContextResolver:
    """Builds the caller identity from Databricks Apps proxy headers.

    The forwarded email is the workspace userName; the preferred-username header carries the
    display name. Outside Databricks Apps (local development) there are no proxy headers, so the
    caller is DEV_USER_EMAIL, or else the identity of the local CLI profile.

    Args:
        config: App configuration (``dev_user_email``).
        factory: Workspace client factory used to resolve the local CLI identity.
        cache: TTL cache for the local CLI identity.

    Attributes:
        EMAIL_HEADER: Header carrying the caller's email / workspace userName.
        DISPLAY_NAME_HEADER: Header carrying the caller's display name.
        TOKEN_HEADER: Header carrying the caller's forwarded OAuth access token.
    """

    EMAIL_HEADER = "x-forwarded-email"
    DISPLAY_NAME_HEADER = "x-forwarded-preferred-username"
    TOKEN_HEADER = "x-forwarded-access-token"

    def __init__(self, config: AppConfig, factory: WorkspaceClientFactory, cache: TtlCache) -> None:
        self._config = config
        self._factory = factory
        self._cache = cache

    def resolve(self, headers: Mapping[str, str]) -> RequestContext:
        """Build the request identity from proxy headers or the local fallback.

        Args:
            headers: Incoming request headers.

        Returns:
            The caller's identity; the access token is set only when forwarded by the proxy.

        Raises:
            UnauthorizedError: If there is no forwarded email and no local identity.
        """
        email = headers.get(self.EMAIL_HEADER)
        if email:
            return RequestContext(
                email=email,
                user_name=email,
                display_name=headers.get(self.DISPLAY_NAME_HEADER),
                access_token=headers.get(self.TOKEN_HEADER),
            )
        local_user = self._local_user()
        if not local_user:
            raise UnauthorizedError("No user identity on the request")
        return RequestContext(email=local_user, user_name=local_user)

    def _local_user(self) -> str | None:
        """Return DEV_USER_EMAIL, else the cached userName of the local CLI profile."""
        if self._config.dev_user_email:
            return self._config.dev_user_email
        return self._cache.get_or_load(
            ("local-user",), lambda: self._factory.app().current_user.me().user_name
        )
