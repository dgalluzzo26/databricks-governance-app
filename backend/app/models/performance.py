"""Models for request elapsed-time metrics, the health monitor, and scaling recommendations."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field

from backend.app.models.base import ApiModel


class RequestMetric(ApiModel):
    """One API request's elapsed time, split by dependency.

    Attributes:
        recorded_at: When the request finished.
        method: HTTP method.
        route: Route template (e.g. ``/api/access-requests/{request_id}/decision``).
        activity: Friendly activity name from the catalog.
        status_code: HTTP status returned.
        duration_ms: Total wall time.
        warehouse_ms: Time waiting on SQL warehouse statements.
        warehouse_calls: SQL warehouse statements issued.
        lakebase_ms: Time spent on Lakebase queries.
        lakebase_calls: Lakebase queries issued.
        user_name: Caller, when known.
    """

    recorded_at: datetime
    method: str
    route: str
    activity: str
    status_code: int
    duration_ms: float
    warehouse_ms: float = 0.0
    warehouse_calls: int = 0
    lakebase_ms: float = 0.0
    lakebase_calls: int = 0
    user_name: str | None = None

    @property
    def app_ms(self) -> float:
        """Time not attributed to the warehouse or Lakebase (Python work + REST API calls)."""
        return max(0.0, self.duration_ms - self.warehouse_ms - self.lakebase_ms)


class ActivityStats(ApiModel):
    """Latency statistics for one activity over the window.

    Attributes:
        activity: Friendly name.
        method: HTTP method.
        route: Route template.
        expected_ms: Threshold above which a request counts as slow.
        count: Requests in the window.
        error_count: Requests with status >= 500.
        slow_count: Requests slower than ``expected_ms``.
        avg_ms: Mean total time.
        p50_ms: Median total time.
        p95_ms: 95th percentile total time.
        max_ms: Slowest request.
        avg_warehouse_ms: Mean SQL warehouse time.
        avg_lakebase_ms: Mean Lakebase time.
        avg_app_ms: Mean remaining (app + REST API) time.
    """

    activity: str
    method: str
    route: str
    expected_ms: float
    count: int
    error_count: int
    slow_count: int
    avg_ms: float
    p50_ms: float
    p95_ms: float
    max_ms: float
    avg_warehouse_ms: float
    avg_lakebase_ms: float
    avg_app_ms: float


class LatencyBucket(ApiModel):
    """Hourly latency roll-up across all activities.

    Attributes:
        hour: Bucket start (UTC).
        count: Requests in the hour.
        slow_count: Slow requests in the hour.
        p95_ms: 95th percentile total time.
        avg_warehouse_ms: Mean SQL warehouse time.
        avg_lakebase_ms: Mean Lakebase time.
        avg_app_ms: Mean remaining time.
        peak_concurrency: Most requests in flight at once during the hour.
    """

    hour: datetime
    count: int
    slow_count: int
    p95_ms: float
    avg_warehouse_ms: float
    avg_lakebase_ms: float
    avg_app_ms: float
    peak_concurrency: int


class RecommendationTarget(StrEnum):
    """Which resource a recommendation is about."""

    APP = "app"
    LAKEBASE = "lakebase"
    WAREHOUSE = "warehouse"
    NONE = "none"


class RecommendationSeverity(StrEnum):
    """How urgent a recommendation is."""

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class ScalingRecommendation(ApiModel):
    """One sizing recommendation derived from slow-request attribution.

    Attributes:
        target: App compute, Lakebase, SQL warehouse, or none.
        severity: Info, warning, or critical.
        title: Short headline.
        detail: Evidence and the suggested change.
    """

    target: RecommendationTarget
    severity: RecommendationSeverity
    title: str
    detail: str


class PerformanceKpis(ApiModel):
    """Headline numbers for the window.

    Attributes:
        total_requests: Requests recorded.
        slow_requests: Requests slower than their activity threshold.
        error_requests: Requests with status >= 500.
        slow_rate: Slow over total, 0..1; None with no traffic.
        p95_ms: 95th percentile total time across all requests.
        warehouse_share: Share of slow-request time spent on the SQL warehouse, 0..1.
        lakebase_share: Share of slow-request time spent on Lakebase, 0..1.
        app_share: Share of slow-request time spent in the app (incl. REST APIs), 0..1.
        peak_concurrency: Most requests in flight at once.
    """

    total_requests: int = 0
    slow_requests: int = 0
    error_requests: int = 0
    slow_rate: float | None = None
    p95_ms: float | None = None
    warehouse_share: float | None = None
    lakebase_share: float | None = None
    app_share: float | None = None
    peak_concurrency: int = 0


class PerformanceSummary(ApiModel):
    """Health-monitor payload for the Performance page.

    Attributes:
        hours: Window length.
        generated_at: When the summary was computed.
        kpis: Headline numbers.
        activities: Per-activity stats, slowest p95 first.
        hourly: Hourly roll-up, oldest first.
        recommendations: Sizing suggestions, most severe first.
    """

    hours: int
    generated_at: datetime
    kpis: PerformanceKpis
    activities: list[ActivityStats] = Field(default_factory=list)
    hourly: list[LatencyBucket] = Field(default_factory=list)
    recommendations: list[ScalingRecommendation] = Field(default_factory=list)


class SlowRequest(ApiModel):
    """One slow request for the paginated slow-request table.

    Attributes:
        recorded_at: When it finished.
        activity: Friendly name.
        method: HTTP method.
        route: Route template.
        status_code: HTTP status.
        duration_ms: Total time.
        expected_ms: Threshold it exceeded.
        warehouse_ms: SQL warehouse time.
        lakebase_ms: Lakebase time.
        app_ms: Remaining time.
        user_name: Caller, when known.
    """

    recorded_at: datetime
    activity: str
    method: str
    route: str
    status_code: int
    duration_ms: float
    expected_ms: float
    warehouse_ms: float
    lakebase_ms: float
    app_ms: float
    user_name: str | None = None


class SlowRequestPage(ApiModel):
    """Paginated slow requests, newest first.

    Attributes:
        items: Page rows.
        total: Slow requests in the window.
        page: 1-based page.
        page_size: Rows per page.
    """

    items: list[SlowRequest] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 25


class CapacitySetting(ApiModel):
    """Current sizing of one resource, read from the workspace.

    Attributes:
        target: App compute, Lakebase, or SQL warehouse.
        name: Resource name or id.
        size: Compute size label (e.g. ``MEDIUM``, ``Medium``, ``1-4 CU``).
        autoscaling: Human-readable autoscaling range, or None when fixed.
        detail: Extra context or the error when the setting could not be read.
        available: False when the app could not read the setting.
    """

    target: RecommendationTarget
    name: str
    size: str | None = None
    autoscaling: str | None = None
    detail: str | None = None
    available: bool = True


class CapacityReport(ApiModel):
    """Current app, Lakebase, and warehouse sizing.

    Attributes:
        settings: One entry per resource.
    """

    settings: list[CapacitySetting] = Field(default_factory=list)
