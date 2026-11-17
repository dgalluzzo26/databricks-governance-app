"""Tracked multi-step tasks: progress for the activity panel and the admin task history.

Routes wrap a long operation in :meth:`TaskService.track`; service code marks phases with the
module-level :func:`step` helper, which is a no-op outside a tracked task, so services need no
extra parameters. A nested ``track`` becomes a step of the outer task. Jobs started from the
app are followed by :meth:`TaskService.start_background`, which polls on a daemon thread.

Progress is written to storage on every step change, so the panel shows it while the request
is still running, and the outcome is kept even if the Apps proxy drops the HTTP response.
"""

from __future__ import annotations

import contextvars
import logging
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.app.core.request_id import current_request_id
from backend.app.models.task import (
    StepStatus,
    TaskPage,
    TaskRecord,
    TaskStatus,
    TaskStep,
)
from backend.app.repositories.base import TaskRepository
from backend.app.services.table_query import TableQuery, sort_and_filter

logger = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 30.0
STALE_AFTER_SECONDS = 180.0
RETENTION_DAYS = 30
PRUNE_INTERVAL_SECONDS = 3600.0
DETAIL_SAVE_INTERVAL_SECONDS = 2.0
MAX_TEXT = 500
PAGE_SIZE_DEFAULT = 25
PAGE_SIZE_MAX = 100
MAX_WINDOW_HOURS = 24 * RETENTION_DAYS

# In-memory readers for the admin task table. Keys match the frontend column keys.
TASK_READERS: dict[str, Callable[[TaskRecord], Any]] = {
    "started": lambda task: task.started_at,
    "task": lambda task: f"{task.title} {task.kind}",
    "status": lambda task: task.status.value,
    "actor": lambda task: task.actor,
    "duration": lambda task: task.duration_seconds if task.duration_seconds is not None else -1,
    "error": lambda task: task.error_message or "",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _short(text: str | None) -> str | None:
    if text is None:
        return None
    return text[:MAX_TEXT]


def _error_parts(exc: BaseException) -> tuple[str, str]:
    """Return ``(code, message)`` for an exception (app errors keep their code)."""
    code = getattr(exc, "code", None)
    message = getattr(exc, "message", None)
    if isinstance(code, str) and isinstance(message, str):
        return code, message
    return "internal_error", f"Unexpected error ({type(exc).__name__}): {exc}"


class TaskHandle:
    """Live, thread-safe view of one running task; persists through its service.

    Args:
        service: Owning service (persists changes).
        record: Initial record.
    """

    def __init__(self, service: TaskService, record: TaskRecord) -> None:
        self._service = service
        self._record = record
        self._lock = threading.Lock()
        self._last_detail_save = 0.0

    @property
    def task_id(self) -> str:
        """The task identifier."""
        return self._record.task_id

    def snapshot(self) -> TaskRecord:
        """Return a copy of the current record."""
        with self._lock:
            return self._record.model_copy(deep=True)

    def add_step(self, name: str) -> int:
        """Start a step and persist it; returns its index."""
        with self._lock:
            now = _now()
            depth = sum(1 for s in self._record.steps if s.status == StepStatus.RUNNING)
            self._record.steps.append(TaskStep(name=_short(name) or name, started_at=now, depth=depth))
            self._record.updated_at = now
            index = len(self._record.steps) - 1
        self._service._save(self)  # noqa: SLF001
        return index

    def finish_step(self, index: int, status: StepStatus, detail: str | None = None) -> None:
        """Finish a step as done or failed and persist it."""
        with self._lock:
            now = _now()
            step = self._record.steps[index]
            step.status = status
            step.finished_at = now
            if detail is not None:
                step.detail = _short(detail)
            self._record.updated_at = now
        self._service._save(self)  # noqa: SLF001

    def set_detail(self, detail: str) -> None:
        """Set the running step's progress note; persisted at most every couple of seconds."""
        with self._lock:
            running = [s for s in self._record.steps if s.status == StepStatus.RUNNING]
            if not running:
                return
            running[-1].detail = _short(detail)
            self._record.updated_at = _now()
            due = time.monotonic() - self._last_detail_save >= DETAIL_SAVE_INTERVAL_SECONDS
            if due:
                self._last_detail_save = time.monotonic()
        if due:
            self._service._save(self)  # noqa: SLF001

    def set_summary(self, summary: str) -> None:
        """Set the one-line outcome shown when the task finishes (saved with the finish)."""
        with self._lock:
            self._record.summary = _short(summary)

    def touch(self) -> None:
        """Refresh ``updated_at`` (heartbeat)."""
        with self._lock:
            self._record.updated_at = _now()

    def complete(self, status: TaskStatus, *, code: str | None = None, message: str | None = None) -> None:
        """Finish the task; any still-running step takes the task's outcome."""
        with self._lock:
            now = _now()
            step_status = StepStatus.DONE if status == TaskStatus.SUCCEEDED else StepStatus.FAILED
            for step in self._record.steps:
                if step.status == StepStatus.RUNNING:
                    step.status = step_status
                    step.finished_at = now
                    if status == TaskStatus.FAILED and message and not step.detail:
                        step.detail = _short(message)
            self._record.status = status
            self._record.finished_at = now
            self._record.updated_at = now
            self._record.error_code = code
            self._record.error_message = _short(message)
        self._service._save(self)  # noqa: SLF001


_current: ContextVar[TaskHandle | None] = ContextVar("current_task", default=None)


def current_task() -> TaskHandle | None:
    """Return the task tracked in this context, or None."""
    return _current.get()


@contextmanager
def step(name: str) -> Iterator[None]:
    """Mark a phase of the current task; a no-op when no task is being tracked.

    Args:
        name: What the phase does, for example ``Regenerate apply_mask_rule UDF``.
    """
    handle = _current.get()
    if handle is None:
        yield
        return
    index = handle.add_step(name)
    try:
        yield
    except BaseException as exc:
        handle.finish_step(index, StepStatus.FAILED, _error_parts(exc)[1])
        raise
    handle.finish_step(index, StepStatus.DONE)


def step_detail(detail: str) -> None:
    """Update the running step's progress note (for example ``12 of 40 grants``)."""
    handle = _current.get()
    if handle is not None:
        handle.set_detail(detail)


def task_summary(summary: str) -> None:
    """Set the current task's one-line outcome."""
    handle = _current.get()
    if handle is not None:
        handle.set_summary(summary)


class TaskService:
    """Creates, persists, and lists tracked tasks.

    Args:
        repository: Returns the task repository (resolved lazily so storage can initialize).
        heartbeat_seconds: How often running tasks refresh ``updated_at``.
        stale_after_seconds: Running tasks without a heartbeat this long are marked failed.
    """

    def __init__(
        self,
        repository: Callable[[], TaskRepository],
        *,
        heartbeat_seconds: float = HEARTBEAT_SECONDS,
        stale_after_seconds: float = STALE_AFTER_SECONDS,
    ) -> None:
        self._repository = repository
        self._heartbeat = heartbeat_seconds
        self._stale_after = stale_after_seconds
        self._active: dict[str, TaskHandle] = {}
        self._active_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_prune: float | None = None

    # -- tracking ----------------------------------------------------------------------

    @contextmanager
    def track(self, kind: str, title: str, actor: str, *, link: str | None = None) -> Iterator[TaskHandle]:
        """Track the enclosed block as one task (or as a step of an enclosing task).

        Args:
            kind: Stable machine label, for example ``mask_rules.save``.
            title: Friendly label shown in the UI.
            actor: User who started the task.
            link: Optional app path for the related page.

        Yields:
            The task handle.

        Raises:
            Exception: Whatever the block raises; the task is recorded as failed first.
        """
        parent = _current.get()
        if parent is not None:
            with step(title):
                yield parent
            return
        handle = self._open(kind, title, actor, link)
        token = _current.set(handle)
        try:
            yield handle
        except BaseException as exc:
            code, message = _error_parts(exc)
            handle.complete(TaskStatus.FAILED, code=code, message=message)
            raise
        else:
            handle.complete(TaskStatus.SUCCEEDED)
        finally:
            _current.reset(token)
            self._close(handle)

    def start_background(
        self,
        kind: str,
        title: str,
        actor: str,
        work: Callable[[TaskHandle], None],
        *,
        link: str | None = None,
    ) -> str:
        """Run ``work`` as a tracked task on a daemon thread and return the task id.

        Args:
            kind: Stable machine label.
            title: Friendly label.
            actor: User who started the task.
            work: Callable doing the work; use :func:`step` inside it.
            link: Optional app path for the related page.

        Returns:
            The new task id (the task is already persisted as running).
        """
        handle = self._open(kind, title, actor, link)
        context = contextvars.copy_context()

        def run() -> None:
            token = _current.set(handle)
            try:
                work(handle)
            except Exception as exc:  # noqa: BLE001 - recorded on the task
                code, message = _error_parts(exc)
                logger.warning("Background task %s (%s) failed: %s", handle.task_id, kind, message)
                handle.complete(TaskStatus.FAILED, code=code, message=message)
            else:
                handle.complete(TaskStatus.SUCCEEDED)
            finally:
                _current.reset(token)
                self._close(handle)

        threading.Thread(target=context.run, args=(run,), name=f"task-{kind}", daemon=True).start()
        return handle.task_id

    def _open(self, kind: str, title: str, actor: str, link: str | None) -> TaskHandle:
        now = _now()
        record = TaskRecord(
            task_id=uuid.uuid4().hex,
            kind=kind,
            title=_short(title) or kind,
            actor=actor,
            started_at=now,
            updated_at=now,
            request_id=current_request_id(),
            link=link,
        )
        handle = TaskHandle(self, record)
        with self._active_lock:
            self._active[handle.task_id] = handle
        self._save(handle)
        return handle

    def _close(self, handle: TaskHandle) -> None:
        with self._active_lock:
            self._active.pop(handle.task_id, None)

    def _save(self, handle: TaskHandle) -> None:
        """Persist the handle's record; storage failures never break the tracked work."""
        try:
            self._repository().save(handle.snapshot())
        except Exception as exc:  # noqa: BLE001 - progress is best effort
            logger.warning("Could not save task %s progress: %s", handle.task_id, exc)

    # -- reads ---------------------------------------------------------------------------

    def get(self, task_id: str) -> TaskRecord | None:
        """Return one task (live view when it runs in this process)."""
        with self._active_lock:
            live = self._active.get(task_id)
        if live is not None:
            return live.snapshot()
        return self._repository().get(task_id)

    def for_actor(self, actor: str, *, hours: int = 24 * 7, limit: int = 30) -> TaskPage:
        """Return the actor's recent tasks, newest first, with running and failed counts.

        Args:
            actor: User name.
            hours: Window length.
            limit: Most tasks to return.

        Returns:
            The tasks (not paginated beyond ``limit``).
        """
        tasks = self._list(hours, actor=actor)
        cap = max(1, min(limit, PAGE_SIZE_MAX))
        return TaskPage(
            items=tasks[:cap],
            total=len(tasks),
            page=1,
            page_size=cap,
            running=sum(1 for t in tasks if t.status == TaskStatus.RUNNING),
            failed=sum(1 for t in tasks if t.status == TaskStatus.FAILED),
        )

    def page(
        self,
        *,
        hours: int = 24,
        status: str | None = None,
        page: int = 1,
        page_size: int = PAGE_SIZE_DEFAULT,
        table_query: TableQuery | None = None,
    ) -> TaskPage:
        """One page of every user's tasks for the admin history.

        Args:
            hours: Window length.
            status: Optional ``running``, ``succeeded`` or ``failed`` filter.
            page: 1-based page.
            page_size: Rows per page.
            table_query: Optional column sort and filters.

        Returns:
            The page with totals and window-wide running and failed counts.
        """
        tasks = self._list(hours)
        running = sum(1 for t in tasks if t.status == TaskStatus.RUNNING)
        failed = sum(1 for t in tasks if t.status == TaskStatus.FAILED)
        wanted = (status or "").strip().lower()
        if wanted:
            tasks = [t for t in tasks if t.status.value == wanted]
        rows = sort_and_filter(tasks, table_query or TableQuery(), TASK_READERS)
        size = min(max(1, page_size), PAGE_SIZE_MAX)
        start = (max(1, page) - 1) * size
        return TaskPage(
            items=rows[start : start + size],
            total=len(rows),
            page=max(1, page),
            page_size=size,
            running=running,
            failed=failed,
        )

    def _list(self, hours: int, *, actor: str | None = None) -> list[TaskRecord]:
        window = min(max(1, hours), MAX_WINDOW_HOURS)
        stored = self._repository().list_since(_now() - timedelta(hours=window), actor=actor)
        with self._active_lock:
            live = {task_id: h.snapshot() for task_id, h in self._active.items()}
        merged = [live.pop(t.task_id, t) for t in stored]
        wanted = actor.lower() if actor else None
        merged.extend(t for t in live.values() if wanted is None or t.actor.lower() == wanted)
        return sorted(merged, key=lambda t: t.started_at, reverse=True)

    # -- lifecycle -----------------------------------------------------------------------

    def start(self) -> None:
        """Start the heartbeat thread (idempotent)."""
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="task-heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the heartbeat thread."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    def tick(self) -> None:
        """Heartbeat running tasks, fail stale ones, and prune old history (one pass)."""
        with self._active_lock:
            handles = list(self._active.values())
        for handle in handles:
            handle.touch()
            self._save(handle)
        try:
            now = _now()
            stale = self._repository().mark_stale(now - timedelta(seconds=self._stale_after), now)
            if stale:
                logger.warning("Marked %d stale task(s) as failed", stale)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not mark stale tasks: %s", exc)
        self._maybe_prune()

    def _maybe_prune(self) -> None:
        now = time.monotonic()
        if self._last_prune is not None and now - self._last_prune < PRUNE_INTERVAL_SECONDS:
            return
        self._last_prune = now
        try:
            self._repository().prune(_now() - timedelta(days=RETENTION_DAYS))
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not prune task history: %s", exc)

    def _run(self) -> None:
        while not self._stop.wait(self._heartbeat):
            self.tick()
