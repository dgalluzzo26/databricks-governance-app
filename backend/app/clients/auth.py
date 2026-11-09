from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from databricks.sdk import WorkspaceClient

from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.config import AuthMode
from backend.app.core.errors import UnauthorizedError


@dataclass(frozen=True)
class RequestContext:
    """Identity of the caller as forwarded by the Databricks Apps proxy."""

    email: str
    user_name: str
    display_name: str | None = None
    access_token: str | None = None


class AuthStrategy(ABC):
    """Chooses which identity Databricks API calls run as for a request."""

    mode: AuthMode

    def __init__(self, factory: WorkspaceClientFactory) -> None:
        self._factory = factory

    @abstractmethod
    def client_for(self, ctx: RequestContext) -> WorkspaceClient: ...


class ServicePrincipalAuthStrategy(AuthStrategy):
    mode = AuthMode.SERVICE_PRINCIPAL

    def client_for(self, ctx: RequestContext) -> WorkspaceClient:
        return self._factory.service_principal()


class OnBehalfOfUserAuthStrategy(AuthStrategy):
    """Runs reads as the end user so Unity Catalog applies their own permissions."""

    mode = AuthMode.ON_BEHALF_OF_USER

    def client_for(self, ctx: RequestContext) -> WorkspaceClient:
        if not ctx.access_token:
            raise UnauthorizedError("User access token is missing; enable user authorization for the app")
        return self._factory.for_user(ctx.access_token)


def build_auth_strategy(mode: AuthMode, factory: WorkspaceClientFactory) -> AuthStrategy:
    strategies: dict[AuthMode, type[AuthStrategy]] = {
        AuthMode.SERVICE_PRINCIPAL: ServicePrincipalAuthStrategy,
        AuthMode.ON_BEHALF_OF_USER: OnBehalfOfUserAuthStrategy,
    }
    return strategies[mode](factory)
