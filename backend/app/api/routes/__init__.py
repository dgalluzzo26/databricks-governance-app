from fastapi import APIRouter

from backend.app.api.routes import app_info, health, me

api_router = APIRouter(prefix="/api")
api_router.include_router(health.router)
api_router.include_router(me.router)
api_router.include_router(app_info.router)
