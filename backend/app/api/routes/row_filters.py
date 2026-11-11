"""Row-filter dimensions, packs, and attachments under ``/api/row-filters``.

Any signed-in user may list dimensions. Creating/updating dimensions, packs,
seed preview, and table attachments require app admin.
"""

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
    """List configured row-filter dimensions.

    Any signed-in user.

    Returns:
        All row-filter dimension definitions.
    """
    return container.row_filters.list_dimensions()


@router.put("/dimensions", response_model=RowFilterDimension)
def upsert_dimension(
    body: UpsertRowFilterDimensionRequest, admin: Admin, container: Container
) -> RowFilterDimension:
    """Create or update a row-filter dimension.

    Requires app admin.

    Args:
        body: Dimension key, SQL, and related metadata.
        admin: Authenticated app administrator (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        The persisted dimension.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    return container.row_filters.upsert_dimension(body, admin)


@router.delete("/dimensions/{key}", status_code=204)
def delete_dimension(key: str, admin: Admin, container: Container) -> None:
    """Delete a row-filter dimension by key.

    Requires app admin.

    Args:
        key: Dimension key to remove.
        admin: Authenticated app administrator (dependency injection).
        container: Process-scoped service container (dependency injection).

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
        NotFoundError: If the dimension does not exist (HTTP 404).
    """
    container.row_filters.delete_dimension(key, admin)


@router.post("/dimensions/{key}/refresh", response_model=RowFilterDimension)
def refresh_dimension(key: str, admin: Admin, container: Container) -> RowFilterDimension:
    """Refresh governed tag values for a row-filter dimension.

    Requires app admin.

    Args:
        key: Dimension key to refresh.
        admin: Authenticated app administrator (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        The updated dimension after refresh.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
        NotFoundError: If the dimension does not exist (HTTP 404).
    """
    return container.row_filters.refresh_dimension(key, admin)


@router.post("/seed-preview", response_model=SeedPreview)
def preview_seed(body: SeedPreviewRequest, admin: Admin, container: Container) -> SeedPreview:
    """Preview seed values produced by a value SQL expression.

    Requires app admin.

    Args:
        body: Seed preview request containing ``value_sql``.
        admin: Authenticated app administrator (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        Preview rows/values from evaluating the SQL.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    _ = admin
    return container.row_filters.preview_seed(body.value_sql)


@router.get("/pack", response_model=RowFilterPackStatus)
def pack_status(admin: Admin, container: Container) -> RowFilterPackStatus:
    """Return row-filter pack / UDF / policy status.

    Requires app admin.

    Returns:
        Current pack deployment status.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    _ = admin
    return container.row_filters.pack_status()


@router.post("/pack/ensure", response_model=RowFilterPackStatus)
def ensure_pack(admin: Admin, container: Container) -> RowFilterPackStatus:
    """Ensure the row-filter pack, UDF, and metastore policy exist.

    Requires app admin.

    Returns:
        Pack status after ensure/rebuild.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    return container.row_filters.ensure_pack(admin)


@router.get("/attachments", response_model=RowFilterAttachmentList)
def list_attachments(_: Admin, container: Container) -> RowFilterAttachmentList:
    """List tables/columns with attached row filters.

    Requires app admin.

    Returns:
        All row-filter attachments.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    return container.row_filters.list_attachments()


@router.post("/attachments", response_model=RowFilterAttachment)
def attach(body: AttachRowFilterRequest, admin: Admin, container: Container) -> RowFilterAttachment:
    """Attach a row filter to a table column.

    Requires app admin.

    Args:
        body: Target table/column and dimension to attach.
        admin: Authenticated app administrator (dependency injection).
        container: Process-scoped service container (dependency injection).

    Returns:
        The created attachment record.

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    return container.row_filters.attach(body, admin)


@router.delete("/attachments", status_code=204)
def detach(table_full_name: str, column_name: str, admin: Admin, container: Container) -> None:
    """Detach a row filter from a table column.

    Requires app admin.

    Args:
        table_full_name: Fully qualified table name.
        column_name: Column whose row filter should be removed.
        admin: Authenticated app administrator (dependency injection).
        container: Process-scoped service container (dependency injection).

    Raises:
        ForbiddenError: If the caller is not an app admin (HTTP 403).
    """
    container.row_filters.detach(table_full_name, column_name, admin)
