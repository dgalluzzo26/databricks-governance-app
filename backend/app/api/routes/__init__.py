"""Aggregate API routers under the ``/api`` prefix.

Includes health, identity, policies, row filters, mask rules, access explorer,
and related governance endpoints.
"""

from fastapi import APIRouter

from backend.app.api.routes import (
    access_explorer,
    access_requests,
    app_info,
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

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(me.router)
api_router.include_router(directory.router)
api_router.include_router(app_info.router)
api_router.include_router(help_routes.router)
api_router.include_router(policies.router)
api_router.include_router(policy_exceptions.router)
api_router.include_router(identity_attributes.router)
api_router.include_router(access_requests.router)
api_router.include_router(row_filters.router)
api_router.include_router(mask_rules.router)
api_router.include_router(access_explorer.router)
api_router.include_router(data_access.router)
