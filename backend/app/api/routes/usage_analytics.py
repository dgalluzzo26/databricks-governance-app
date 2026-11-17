"""Usage analytics routes for data access tracking under ``/api/usage-analytics``."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, Response

from backend.app.api.deps import Admin, Container
from backend.app.models.usage_analytics import (
    AccessNoAccessList,
    AccessTableDetail,
    AccessTableList,
    AccessUnusedList,
    UsageOverview,
    UsageRefreshResponse,
    UsageTableDetail,
    UsageTableList,
    UsageUserList,
)

router = APIRouter(prefix="/usage-analytics", tags=["usage-analytics"])

DaysQuery = Annotated[int, Query(ge=1, le=90)]


@router.get("/overview", response_model=UsageOverview)
def get_usage_overview(
    _: Admin,
    container: Container,
    days: DaysQuery = 30,
) -> UsageOverview:
    """Load data usage overview: headline KPIs, daily trends, top tables/users, and sensitive column reads."""
    return container.usage_analytics.overview(days=days)


@router.get("/tables", response_model=UsageTableList)
def list_usage_tables(
    _: Admin,
    container: Container,
    days: DaysQuery = 30,
    q: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=200),
) -> UsageTableList:
    """List tables accessed during the time window, ordered by reads."""
    return container.usage_analytics.list_tables(days=days, q=q, limit=limit)


@router.get("/users", response_model=UsageUserList)
def list_usage_users(
    _: Admin,
    container: Container,
    days: DaysQuery = 30,
    q: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=200),
) -> UsageUserList:
    """List users who accessed enrolled tables during the time window, ordered by reads."""
    return container.usage_analytics.list_users(days=days, q=q, limit=limit)


@router.get("/table-detail", response_model=UsageTableDetail)
def get_table_detail(
    _: Admin,
    container: Container,
    table_full_name: str = Query(..., min_length=3, max_length=500),
    days: DaysQuery = 30,
) -> UsageTableDetail:
    """Drilldown view for a single table: users who read it, column access modes, and recent read events."""
    return container.usage_analytics.table_detail(table_full_name=table_full_name, days=days)


@router.post("/refresh", response_model=UsageRefreshResponse)
def refresh_usage_rollup(
    admin: Admin,
    container: Container,
) -> UsageRefreshResponse:
    """Trigger an on-demand run of the usage rollup job."""
    return container.usage_analytics.refresh(actor=admin)


@router.get("/export")
def export_usage_csv(
    _: Admin,
    container: Container,
    kind: str = Query("tables", pattern="^(tables|users|sensitive)$"),
    days: DaysQuery = 30,
) -> Response:
    """Export usage data as a CSV file for download."""
    csv_text = container.usage_analytics.export_csv(kind=kind, days=days)
    filename = f"usage_{kind}_{days}d.csv"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/access/tables", response_model=AccessTableList)
def list_access_tables(
    _: Admin,
    container: Container,
    days: DaysQuery = 30,
    q: str | None = Query(None, max_length=200),
    limit: int = Query(50, ge=1, le=200),
) -> AccessTableList:
    """Governed tables with holder counts, unused holders, and readers who hold no grant."""
    return container.usage_access.tables(days=days, q=q, limit=limit)


@router.get("/access/table-detail", response_model=AccessTableDetail)
def get_access_table_detail(
    _: Admin,
    container: Container,
    table_full_name: str = Query(..., min_length=3, max_length=500),
    days: DaysQuery = 30,
) -> AccessTableDetail:
    """Holders of one table with their grants and reads, plus readers who hold no grant."""
    return container.usage_access.table_detail(table_full_name=table_full_name, days=days)


@router.get("/access/unused", response_model=AccessUnusedList)
def list_unused_access(
    _: Admin,
    container: Container,
    days: DaysQuery = 30,
    limit: int = Query(200, ge=1, le=1000),
) -> AccessUnusedList:
    """Holders with zero reads on a governed table in the window."""
    return container.usage_access.unused(days=days, limit=limit)


@router.get("/access/no-access", response_model=AccessNoAccessList)
def list_reads_without_access(
    _: Admin,
    container: Container,
    days: DaysQuery = 30,
    limit: int = Query(200, ge=1, le=1000),
) -> AccessNoAccessList:
    """Users with reads on a governed table who hold no assigned grant there."""
    return container.usage_access.no_access(days=days, limit=limit)


@router.get("/access/export")
def export_access_csv(
    _: Admin,
    container: Container,
    kind: str = Query("tables", pattern="^(tables|holders|unused|no-access)$"),
    days: DaysQuery = 30,
) -> Response:
    """Export an access report as CSV for auditors."""
    csv_text = container.usage_access.export_csv(kind=kind, days=days)
    filename = f"access_{kind}_{days}d.csv"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
