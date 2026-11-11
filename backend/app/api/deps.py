from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from backend.app.clients.auth import RequestContext
from backend.app.core.container import ServiceContainer
from backend.app.core.errors import ForbiddenError
from backend.app.models.user import CurrentUser


def get_container(request: Request) -> ServiceContainer:
    return request.app.state.container


Container = Annotated[ServiceContainer, Depends(get_container)]


def get_request_context(request: Request, container: Container) -> RequestContext:
    return container.request_context.resolve(request.headers)


Context = Annotated[RequestContext, Depends(get_request_context)]


def get_current_user(ctx: Context, container: Container) -> CurrentUser:
    user = container.identity.current_user(ctx)
    if user.is_admin:
        return user.model_copy(update={"is_approver": True})
    return user.model_copy(
        update={"is_approver": container.access_requests.is_category_approver(user)}
    )


User = Annotated[CurrentUser, Depends(get_current_user)]


def require_admin(user: User) -> CurrentUser:
    if not user.is_admin:
        raise ForbiddenError("This action requires app administrator access")
    return user


Admin = Annotated[CurrentUser, Depends(require_admin)]
