from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.deps import Container, User
from backend.app.models.app_info import AppInfo

router = APIRouter(tags=["app"])


@router.get("/app-info", response_model=AppInfo)
def app_info(_: User, container: Container) -> AppInfo:
    return container.app_info.info()
