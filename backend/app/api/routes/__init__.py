"""Aggregate API routers under the ``/api`` prefix.

Includes health, identity, policies, row filters, mask rules, access explorer,
and related governance endpoints.
"""

from fastapi import APIRouter

from backend.app.api.routes import (
    access_explorer,
    access_requests,
    app_info,
    attribute_explorer,
    data_access,
    directory,
    health,
    identity_attributes,
    mask_rules,
    me,
    policies,
    policy_exceptions,
    row_filters,
)
from backend.app.api.routes import (
    help as help_routes,
)

# Feature routers in mount order; the Help API reference walks this list.
ROUTERS: tuple[APIRouter, ...] = (
    health.router,
    me.router,
    directory.router,
    app_info.router,
    help_routes.router,
    policies.router,
    policy_exceptions.router,
    identity_attributes.router,
    access_requests.router,
    row_filters.router,
    mask_rules.router,
    access_explorer.router,
    attribute_explorer.router,
    data_access.router,
)

api_router = APIRouter(prefix="/api")
for _router in ROUTERS:
    api_router.include_router(_router)
