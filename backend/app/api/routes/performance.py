"""Admin health monitor API: request latency, slow requests, and current resource sizing."""

from __future__ import annotations

from fastapi import APIRouter, Query

from backend.app.api.deps import Admin, Container
from backend.app.models.app_cost import AppCostReport
from backend.app.models.performance import CapacityReport, PerformanceSummary, SlowRequestPage
from backend.app.services.app_cost import DEFAULT_DAYS, MAX_DAYS
from backend.app.services.performance import SLOW_PAGE_SIZE_DEFAULT, SLOW_PAGE_SIZE_MAX
from backend.app.services.table_query import parse_table_query

router = APIRouter(prefix="/performance", tags=["performance"])


@router.get("/summary", response_model=PerformanceSummary)
def summary(
    _: Admin,
    container: Container,
    hours: int = Query(default=24, ge=1, le=24 * 14, description="Window in hours (max 14 days)."),
) -> PerformanceSummary:
    """Latency KPIs, per-activity stats, hourly roll-up, and sizing recommendations.

    Requires app admin.

    Args:
        container: Service container.
        hours: Window length in hours.

    Returns:
        The health-monitor payload for the window.
    """
    return container.performance.summary(hours)


@router.get("/slow-requests", response_model=SlowRequestPage)
def slow_requests(
    _: Admin,
    container: Container,
    hours: int = Query(default=24, ge=1, le=24 * 14),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=SLOW_PAGE_SIZE_DEFAULT, ge=1, le=SLOW_PAGE_SIZE_MAX),
    sort: str | None = Query(default=None, description="Column key to sort by (table column)"),
    direction: str | None = Query(default=None, description="asc or desc"),
    filters: str | None = Query(
        default=None, description='JSON object of column key to filter text, e.g. {"status":"fail"}'
    ),
) -> SlowRequestPage:
    """One page of requests slower than their activity's expected time, newest first.

    Requires app admin.

    Args:
        container: Service container.
        hours: Window length in hours.
        page: 1-based page.
        page_size: Rows per page.
        sort: Optional column key to sort by.
        direction: ``asc`` or ``desc``.
        filters: Optional JSON object of column filters.

    Returns:
        The page of slow requests with the total count.
    """
    return container.performance.slow_requests(
        hours=hours, page=page, page_size=page_size, table_query=parse_table_query(sort, direction, filters)
    )


@router.get("/capacity", response_model=CapacityReport)
def capacity(_: Admin, container: Container) -> CapacityReport:
    """Current app compute size, Lakebase autoscaling range, and SQL warehouse size.

    Requires app admin. Reads use the Account Admin SP; each resource reports its own error.

    Args:
        container: Service container.

    Returns:
        Sizing per resource.
    """
    return container.performance.capacity()


@router.get("/app-cost", response_model=AppCostReport)
def app_cost(
    _: Admin,
    container: Container,
    days: int = Query(default=DEFAULT_DAYS, ge=1, le=MAX_DAYS, description="Window in UTC days (max 30)."),
) -> AppCostReport:
    """Daily app compute, and warehouse plus job cost per sync, from billing system tables.

    Requires app admin. Reads use the Account Admin SP (billing admin). Results are cached for
    15 minutes. A failed read returns ``available=false`` with the reason.

    Args:
        container: Service container.
        days: Window length in UTC days.

    Returns:
        The cost report for the window.
    """
    return container.app_cost.report(days)
