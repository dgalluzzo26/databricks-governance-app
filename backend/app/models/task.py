"""Models for tracked multi-step tasks shown in the activity panel and the admin task history."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import Field, computed_field

from backend.app.models.base import ApiModel
from backend.app.models.performance import RequestMetric


class TaskStatus(StrEnum):
    """Lifecycle of a task."""

    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class StepStatus(StrEnum):
    """Lifecycle of one step inside a task."""

    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


class TaskStep(ApiModel):
    """One step of a task, in the order it started.

    Attributes:
        name: What the step does, for example ``Regenerate apply_mask_rule UDF``.
        status: Running, done, or failed.
        started_at: When the step began.
        finished_at: When it finished; None while running.
        detail: Optional progress or outcome note, for example ``12 of 40 grants``.
        depth: Nesting level; 0 for top-level steps, 1 for steps started inside another.
    """

    name: str
    status: StepStatus = StepStatus.RUNNING
    started_at: datetime
    finished_at: datetime | None = None
    detail: str | None = None
    depth: int = 0


class TaskRecord(ApiModel):
    """One tracked operation and its steps.

    Attributes:
        task_id: Unique identifier.
        kind: Stable machine label, for example ``mask_rules.save``.
        title: Friendly label shown in the UI.
        actor: User who started the task, or ``scheduler``.
        status: Running, succeeded, or failed.
        steps: Steps in start order.
        started_at: When the task began.
        finished_at: When it finished; None while running.
        updated_at: Last progress write or heartbeat; stale running tasks are marked failed.
        error_code: App error code when the task failed.
        error_message: Error message when the task failed.
        request_id: Correlation ID of the request that started the task.
        link: Optional app path for the related page, for example ``/mask-rules``.
        summary: Optional one-line outcome, for example ``42 grants applied``.
    """

    task_id: str
    kind: str
    title: str
    actor: str
    status: TaskStatus = TaskStatus.RUNNING
    steps: list[TaskStep] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime | None = None
    updated_at: datetime
    error_code: str | None = None
    error_message: str | None = None
    request_id: str | None = None
    link: str | None = None
    summary: str | None = None

    @computed_field
    @property
    def duration_seconds(self) -> float | None:
        """Wall-clock seconds from start to finish, or None while running."""
        if self.finished_at is None:
            return None
        return round(max(0.0, (self.finished_at - self.started_at).total_seconds()), 1)


class TaskPage(ApiModel):
    """One page of tasks, newest first unless sorted.

    Attributes:
        items: Tasks on this page.
        total: Matching tasks across all pages.
        page: 1-based page number.
        page_size: Rows per page.
        running: Tasks still running in the window (for badges).
        failed: Failed tasks in the window.
    """

    items: list[TaskRecord] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 25
    running: int = 0
    failed: int = 0


class RequestRecord(ApiModel):
    """One stored API request as shown in the activity panel and admin request history.

    Attributes:
        recorded_at: When the request finished.
        method: HTTP method.
        route: Route template.
        activity: Friendly activity name.
        status_code: HTTP status returned.
        ok: True for status below 400.
        duration_ms: Total wall time.
        user_name: Caller, when known.
        request_id: Correlation ID.
        error_code: App error code for failed requests.
        error_message: Error message for failed requests.
    """

    recorded_at: datetime
    method: str
    route: str
    activity: str
    status_code: int
    ok: bool
    duration_ms: float
    user_name: str | None = None
    request_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    @classmethod
    def from_metric(cls, metric: RequestMetric) -> RequestRecord:
        """Build the view model from a stored request metric."""
        return cls(
            recorded_at=metric.recorded_at,
            method=metric.method,
            route=metric.route,
            activity=metric.activity,
            status_code=metric.status_code,
            ok=metric.status_code < 400,
            duration_ms=metric.duration_ms,
            user_name=metric.user_name,
            request_id=metric.request_id,
            error_code=metric.error_code,
            error_message=metric.error_message,
        )


class RequestPage(ApiModel):
    """One page of stored requests.

    Attributes:
        items: Requests on this page.
        total: Matching requests across all pages.
        page: 1-based page number.
        page_size: Rows per page.
        succeeded: Requests with status below 400 in the window (before the status filter).
        failed: Requests with status 400 or above in the window (before the status filter).
    """

    items: list[RequestRecord] = Field(default_factory=list)
    total: int = 0
    page: int = 1
    page_size: int = 25
    succeeded: int = 0
    failed: int = 0
