"""Follow Databricks Jobs started from the app as background tasks in the activity panel.

The HTTP request that starts a Job returns at once (the Apps proxy times out after about two
minutes), so these followers poll on a daemon thread and record the outcome on the task.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from backend.app.core.errors import StatementTimeoutError, UpstreamError
from backend.app.models.data_access import DataAccessRun, SyncRunStatus
from backend.app.services.tasks import TaskHandle, TaskService, step, step_detail, task_summary

POLL_SECONDS = 10.0
MAX_FOLLOW_SECONDS = 4 * 3600.0
TERMINAL_LIFE_CYCLE_STATES = frozenset({"TERMINATED", "SKIPPED", "INTERNAL_ERROR"})


def _enum_text(value: Any) -> str:
    """Return an SDK enum's value (or the plain string), upper-cased; empty when missing."""
    raw = getattr(value, "value", value)
    return str(raw).upper() if raw else ""


def _elapsed(started: float) -> str:
    seconds = int(time.monotonic() - started)
    return f"{seconds // 60}m {seconds % 60:02d}s" if seconds >= 60 else f"{seconds}s"


def follow_data_access_run(
    tasks: TaskService,
    get_run: Callable[[str], DataAccessRun],
    run: DataAccessRun,
    actor: str,
    *,
    poll_seconds: float = POLL_SECONDS,
    max_seconds: float = MAX_FOLLOW_SECONDS,
) -> str:
    """Track a data-access sync Job run until its history row finishes.

    Args:
        tasks: Task service.
        get_run: Reads the run's history row (``DataAccessService.get_run``).
        run: The ``running`` row returned when the Job was triggered.
        actor: Admin who started the sync.
        poll_seconds: Delay between reads.
        max_seconds: Give up following (the run keeps going) after this long.

    Returns:
        The task id.
    """
    label = "apply" if run.mode == "apply" else "dry run"

    def work(_: TaskHandle) -> None:
        with step("Start the data-access sync Job"):
            pass
        started = time.monotonic()
        with step("Wait for the sync Job to finish"):
            current = get_run(run.run_id)
            while current.status == SyncRunStatus.RUNNING:
                if time.monotonic() - started > max_seconds:
                    raise StatementTimeoutError(
                        "Stopped following the sync Job; check Data access for the outcome"
                    )
                step_detail(f"Running for {_elapsed(started)}")
                time.sleep(poll_seconds)
                current = get_run(run.run_id)
        if current.status == SyncRunStatus.FAILED:
            raise UpstreamError(current.error or "The data-access sync Job failed")
        summary = current.summary
        if summary is not None:
            task_summary(
                f"{summary.grants_to_add} grants to add, {summary.grants_to_revoke} to revoke, "
                f"{summary.groups_to_create} groups to create"
            )

    return tasks.start_background(
        "data_access.sync", f"Data-access sync ({label})", actor, work, link="/data-access"
    )


def follow_job_run(
    tasks: TaskService,
    jobs: Callable[[], Any],
    run_id: int,
    *,
    kind: str,
    title: str,
    actor: str,
    link: str | None = None,
    poll_seconds: float = POLL_SECONDS,
    max_seconds: float = MAX_FOLLOW_SECONDS,
) -> str:
    """Track a Databricks Job run until it reaches a terminal state.

    Args:
        tasks: Task service.
        jobs: Returns the SDK jobs API (``client.jobs``).
        run_id: Job run id from ``jobs.run_now``.
        kind: Task kind, for example ``usage.rollup``.
        title: Friendly task title.
        actor: User who started the run.
        link: Optional app path for the related page.
        poll_seconds: Delay between reads.
        max_seconds: Give up following (the run keeps going) after this long.

    Returns:
        The task id.
    """

    def work(_: TaskHandle) -> None:
        started = time.monotonic()
        with step(f"Wait for Job run {run_id} to finish"):
            while True:
                run = jobs().get_run(run_id=run_id)
                state = getattr(run, "state", None)
                life_cycle = _enum_text(getattr(state, "life_cycle_state", None))
                if life_cycle in TERMINAL_LIFE_CYCLE_STATES:
                    break
                if time.monotonic() - started > max_seconds:
                    raise StatementTimeoutError(
                        f"Stopped following Job run {run_id}; check the Job for the outcome"
                    )
                job_tasks = list(getattr(run, "tasks", None) or [])
                finished = sum(
                    1
                    for t in job_tasks
                    if _enum_text(getattr(getattr(t, "state", None), "life_cycle_state", None))
                    in TERMINAL_LIFE_CYCLE_STATES
                )
                progress = f", {finished} of {len(job_tasks)} job tasks done" if job_tasks else ""
                step_detail(f"{(life_cycle or 'pending').lower()} for {_elapsed(started)}{progress}")
                time.sleep(poll_seconds)
        result = _enum_text(getattr(state, "result_state", None))
        if result != "SUCCESS":
            message = getattr(state, "state_message", None) or "no message"
            raise UpstreamError(
                f"Job run {run_id} finished {result.lower() or life_cycle.lower()}: {message}"
            )
        task_summary(f"Job run {run_id} succeeded in {_elapsed(started)}")

    return tasks.start_background(kind, title, actor, work, link=link)
