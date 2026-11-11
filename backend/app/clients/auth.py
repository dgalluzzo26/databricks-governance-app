"""Per-request caller identity and strategies choosing which identity API calls run as."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from databricks.sdk import WorkspaceClient

from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.config import AuthMode
from backend.app.core.errors import UnauthorizedError


@dataclass(frozen=True)
class RequestContext:
    """Identity of the caller as forwarded by the Databricks Apps proxy.

    Attributes:
        email: Caller's email address.
        user_name: Caller's Databricks user name.
        display_name: Human-readable name, or None when not forwarded.
        access_token: Caller's OAuth access token when user authorization is enabled for
            the app; None otherwise (and in local development).
    """

    email: str
    user_name: str
    display_name: str | None = None
    access_token: str | None = None


class AuthStrategy(ABC):
    """Chooses which identity Databricks API calls run as for a request.

    Args:
        factory: Factory that builds or returns cached workspace clients.

    Attributes:
        mode: Auth mode implemented by the strategy.
    """

    mode: AuthMode

    def __init__(self, factory: WorkspaceClientFactory) -> None:
        self._factory = factory

    @abstractmethod
    def client_for(self, ctx: RequestContext) -> WorkspaceClient:
        """Return the workspace client to use for the caller's request.

        Args:
            ctx: Identity of the caller.

        Returns:
            A workspace client authenticated as the identity this strategy selects.
        """


class ServicePrincipalAuthStrategy(AuthStrategy):
    """Runs every request as the service principal, regardless of the caller.

    Uses the Account Admin SP when configured, otherwise the Databricks Apps SP.
    """

    mode = AuthMode.SERVICE_PRINCIPAL

    def client_for(self, ctx: RequestContext) -> WorkspaceClient:
        """Return the cached service principal client.

        Args:
            ctx: Identity of the caller (unused).

        Returns:
            The shared service principal workspace client.
        """
        return self._factory.service_principal()


class OnBehalfOfUserAuthStrategy(AuthStrategy):
    """Runs reads as the end user so Unity Catalog applies their own permissions."""

    mode = AuthMode.ON_BEHALF_OF_USER

    def client_for(self, ctx: RequestContext) -> WorkspaceClient:
        """Return a new client authenticated with the caller's forwarded access token.

        Args:
            ctx: Identity of the caller, including the access token.

        Returns:
            A workspace client acting as the end user (not cached).

        Raises:
            UnauthorizedError: If the request carries no user access token.
        """
        if not ctx.access_token:
            raise UnauthorizedError("User access token is missing; enable user authorization for the app")
        return self._factory.for_user(ctx.access_token)


def build_auth_strategy(mode: AuthMode, factory: WorkspaceClientFactory) -> AuthStrategy:
    """Create the auth strategy for the configured mode.

    Args:
        mode: Configured auth mode.
        factory: Workspace client factory passed to the strategy.

    Returns:
        The strategy implementing ``mode``.
    """
    strategies: dict[AuthMode, type[AuthStrategy]] = {
        AuthMode.SERVICE_PRINCIPAL: ServicePrincipalAuthStrategy,
        AuthMode.ON_BEHALF_OF_USER: OnBehalfOfUserAuthStrategy,
    }
    return strategies[mode](factory)
