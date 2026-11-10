from fastapi import APIRouter

from backend.app.api.routes import (
    access_explorer,
    access_requests,
    app_info,
    health,
    identity_attributes,
    mask_rules,
    me,
    policies,
    row_filters,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(me.router)
api_router.include_router(app_info.router)
api_router.include_router(policies.router)
api_router.include_router(identity_attributes.router)
api_router.include_router(access_requests.router)
api_router.include_router(row_filters.router)
api_router.include_router(mask_rules.router)
api_router.include_router(access_explorer.router)
