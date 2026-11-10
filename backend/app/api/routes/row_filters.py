from __future__ import annotations

from fastapi import APIRouter

from backend.app.api.deps import Admin, Container, User
from backend.app.models.row_filter import (
    AttachRowFilterRequest,
    RowFilterAttachment,
    RowFilterAttachmentList,
    RowFilterDimension,
    RowFilterDimensionList,
    RowFilterPackStatus,
    SeedPreview,
    SeedPreviewRequest,
    UpsertRowFilterDimensionRequest,
)

router = APIRouter(prefix="/row-filters", tags=["row-filters"])


@router.get("/dimensions", response_model=RowFilterDimensionList)
def list_dimensions(_: User, container: Container) -> RowFilterDimensionList:
    return container.row_filters.list_dimensions()


@router.put("/dimensions", response_model=RowFilterDimension)
def upsert_dimension(
    body: UpsertRowFilterDimensionRequest, admin: Admin, container: Container
) -> RowFilterDimension:
    return container.row_filters.upsert_dimension(body, admin)


@router.delete("/dimensions/{key}", status_code=204)
def delete_dimension(key: str, admin: Admin, container: Container) -> None:
    container.row_filters.delete_dimension(key, admin)


@router.post("/dimensions/{key}/refresh", response_model=RowFilterDimension)
def refresh_dimension(key: str, admin: Admin, container: Container) -> RowFilterDimension:
    return container.row_filters.refresh_dimension(key, admin)


@router.post("/seed-preview", response_model=SeedPreview)
def preview_seed(body: SeedPreviewRequest, admin: Admin, container: Container) -> SeedPreview:
    _ = admin
    return container.row_filters.preview_seed(body.value_sql)


@router.get("/pack", response_model=RowFilterPackStatus)
def pack_status(admin: Admin, container: Container) -> RowFilterPackStatus:
    _ = admin
    return container.row_filters.pack_status()


@router.post("/pack/ensure", response_model=RowFilterPackStatus)
def ensure_pack(admin: Admin, container: Container) -> RowFilterPackStatus:
    return container.row_filters.ensure_pack(admin)


@router.get("/attachments", response_model=RowFilterAttachmentList)
def list_attachments(_: User, container: Container) -> RowFilterAttachmentList:
    return container.row_filters.list_attachments()


@router.post("/attachments", response_model=RowFilterAttachment)
def attach(body: AttachRowFilterRequest, admin: Admin, container: Container) -> RowFilterAttachment:
    return container.row_filters.attach(body, admin)


@router.delete("/attachments", status_code=204)
def detach(table_full_name: str, column_name: str, admin: Admin, container: Container) -> None:
    container.row_filters.detach(table_full_name, column_name, admin)
