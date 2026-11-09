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
    """

    EMAIL_HEADER = "x-forwarded-email"
    DISPLAY_NAME_HEADER = "x-forwarded-preferred-username"
    TOKEN_HEADER = "x-forwarded-access-token"

    def __init__(self, config: AppConfig, factory: WorkspaceClientFactory, cache: TtlCache) -> None:
        self._config = config
        self._factory = factory
        self._cache = cache

    def resolve(self, headers: Mapping[str, str]) -> RequestContext:
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
        if self._config.dev_user_email:
            return self._config.dev_user_email
        return self._cache.get_or_load(
            ("local-user",), lambda: self._factory.app().current_user.me().user_name
        )
