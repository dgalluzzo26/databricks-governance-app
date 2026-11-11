"""Shared FastAPI dependencies and Annotated injection aliases.

Provides:

* ``get_container`` / ``Container`` — process-scoped ``ServiceContainer`` from app state.
* ``get_request_context`` / ``Context`` — auth headers resolved into ``RequestContext``.
* ``get_current_user`` / ``User`` — signed-in ``CurrentUser``; sets ``is_approver``
  (admins always true; otherwise category-approver lookup).
* ``require_admin`` / ``Admin`` — same as ``User`` but requires ``is_admin`` (403 otherwise).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from backend.app.clients.auth import RequestContext
from backend.app.core.container import ServiceContainer
from backend.app.core.errors import ForbiddenError
from backend.app.models.user import CurrentUser


def get_container(request: Request) -> ServiceContainer:
    """Return the process-scoped service container from app state.

    Args:
        request: Incoming ASGI request whose app holds the container.

    Returns:
        The ``ServiceContainer`` stored on ``request.app.state.container``.
    """
    return request.app.state.container


# Injected service container for route handlers and nested dependencies.
Container = Annotated[ServiceContainer, Depends(get_container)]


def get_request_context(request: Request, container: Container) -> RequestContext:
    """Resolve the caller's auth context from request headers.

    Args:
        request: Incoming request providing auth-related headers.
        container: Service container with the request-context resolver.

    Returns:
        A ``RequestContext`` for the current caller.
    """
    return container.request_context.resolve(request.headers)


# Injected auth/request context derived from headers.
Context = Annotated[RequestContext, Depends(get_request_context)]


def get_current_user(ctx: Context, container: Container) -> CurrentUser:
    """Load the signed-in user and compute ``is_approver``.

    Admins always have ``is_approver=True``. Non-admins get ``is_approver`` from
    the category-approver registry lookup.

    Args:
        ctx: Resolved request context for the caller.
        container: Service container with identity and access-request services.

    Returns:
        ``CurrentUser`` with ``is_approver`` set.
    """
    user = container.identity.current_user(ctx)
    if user.is_admin:
        return user.model_copy(update={"is_approver": True})
    return user.model_copy(
        update={"is_approver": container.access_requests.is_category_approver(user)}
    )


# Any signed-in user (with ``is_approver`` computed).
User = Annotated[CurrentUser, Depends(get_current_user)]


def require_admin(user: User) -> CurrentUser:
    """Require the caller to be an app administrator.

    Args:
        user: Authenticated current user.

    Returns:
        The same user when ``is_admin`` is true.

    Raises:
        ForbiddenError: If the user is not an app admin (HTTP 403).
    """
    if not user.is_admin:
        raise ForbiddenError("This action requires app administrator access")
    return user


# Signed-in app administrator; non-admins receive 403 via ``require_admin``.
Admin = Annotated[CurrentUser, Depends(require_admin)]
