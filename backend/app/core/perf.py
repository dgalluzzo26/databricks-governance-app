"""Per-request elapsed-time tracking and background persistence of request metrics.

The HTTP middleware opens a :class:`RequestTimings` for each API request. Code that talks to
the SQL warehouse or Lakebase wraps the call in :func:`track` so its wall time is attributed to
that dependency. Whatever remains of the request's total time is app time (Python work plus
Databricks REST API calls).

Samples are queued in memory and flushed to storage by a daemon thread, so recording never
adds latency to the request itself.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.app.core.activities import activity_for
from backend.app.models.performance import RequestMetric

logger = logging.getLogger(__name__)

Dependency = Literal["warehouse", "lakebase"]

FLUSH_INTERVAL_SECONDS = 5.0
FLUSH_BATCH_SIZE = 200
QUEUE_MAX = 10_000
RETENTION_DAYS = 14
PRUNE_INTERVAL_SECONDS = 3600.0


@dataclass
class RequestTimings:
    """Mutable accumulator shared by everything that runs for one request.

    Attributes:
        warehouse_ms: Wall time spent waiting on SQL warehouse statements.
        warehouse_calls: Number of SQL warehouse statements.
        lakebase_ms: Wall time spent on Lakebase queries.
        lakebase_calls: Number of Lakebase queries.
    """

    warehouse_ms: float = 0.0
    warehouse_calls: int = 0
    lakebase_ms: float = 0.0
    lakebase_calls: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def add(self, dependency: Dependency, elapsed_ms: float) -> None:
        """Attribute ``elapsed_ms`` to one dependency (thread-safe)."""
        with self._lock:
            if dependency == "warehouse":
                self.warehouse_ms += elapsed_ms
                self.warehouse_calls += 1
            else:
                self.lakebase_ms += elapsed_ms
                self.lakebase_calls += 1


_current: ContextVar[RequestTimings | None] = ContextVar("request_timings", default=None)


def start_request() -> RequestTimings:
    """Open a timing accumulator for the current request context.

    Returns:
        The accumulator; the middleware reads it after the response is produced.
    """
    timings = RequestTimings()
    _current.set(timings)
    return timings


@contextmanager
def track(dependency: Dependency) -> Iterator[None]:
    """Time the enclosed block and attribute it to ``dependency`` for the current request.

    No-op outside a request (startup, the sync Job, background flushes).

    Args:
        dependency: ``warehouse`` or ``lakebase``.
    """
    timings = _current.get()
    if timings is None:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        timings.add(dependency, (time.perf_counter() - started) * 1000)


class MetricsRecorder:
    """Queue request metrics and flush them to storage from a daemon thread.

    Args:
        sink: Persists a batch of metrics (e.g. the storage performance repository).
        pruner: Deletes metrics older than a cutoff; called at most hourly.
        flush_interval: Seconds between flushes when the queue is not full.
    """

    def __init__(
        self,
        sink: Callable[[list[RequestMetric]], None],
        pruner: Callable[[datetime], None] | None = None,
        *,
        flush_interval: float = FLUSH_INTERVAL_SECONDS,
    ) -> None:
        self._sink = sink
        self._pruner = pruner
        self._flush_interval = flush_interval
        self._queue: queue.Queue[RequestMetric] = queue.Queue(maxsize=QUEUE_MAX)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_prune: float | None = None
        self.dropped = 0

    def record(self, metric: RequestMetric) -> None:
        """Enqueue one metric; drops it (and counts the drop) when the queue is full."""
        try:
            self._queue.put_nowait(metric)
        except queue.Full:
            self.dropped += 1

    def start(self) -> None:
        """Start the background flush thread (idempotent)."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="metrics-recorder", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Stop the thread and flush whatever is still queued."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None
        self.flush()

    def flush(self) -> int:
        """Write every queued metric in batches.

        Returns:
            Number of metrics written.
        """
        written = 0
        while True:
            batch: list[RequestMetric] = []
            while len(batch) < FLUSH_BATCH_SIZE:
                try:
                    batch.append(self._queue.get_nowait())
                except queue.Empty:
                    break
            if not batch:
                return written
            try:
                self._sink(batch)
                written += len(batch)
            except Exception as exc:  # noqa: BLE001 - metrics must never break the app
                logger.warning("Could not persist %d request metrics: %s", len(batch), exc)
                return written

    def _maybe_prune(self) -> None:
        if self._pruner is None:
            return
        now = time.monotonic()
        if self._last_prune is not None and now - self._last_prune < PRUNE_INTERVAL_SECONDS:
            return
        self._last_prune = now
        try:
            self._pruner(datetime.now(UTC) - timedelta(days=RETENTION_DAYS))
        except Exception as exc:  # noqa: BLE001 - best effort
            logger.warning("Could not prune request metrics: %s", exc)

    def _run(self) -> None:
        while not self._stop.wait(self._flush_interval):
            self.flush()
            self._maybe_prune()


class RequestTimingMiddleware:
    """ASGI middleware that times every ``/api`` request and queues one metric for it.

    Route templates are resolved after the response so that path parameters collapse to one
    activity (``/api/policies/{policy_id}``). Health probes are skipped to keep the table signal-only.

    Args:
        app: Downstream ASGI application.
        recorder: Returns the active recorder, or None before startup / after shutdown.
        route_template: Resolves a request scope to its route template, or None when unmatched.
    """

    SKIP_PATHS = frozenset({"/api/health"})

    def __init__(
        self,
        app: ASGIApp,
        *,
        recorder: Callable[[], MetricsRecorder | None],
        route_template: Callable[[Scope], str | None],
    ) -> None:
        self.app = app
        self._recorder = recorder
        self._route_template = route_template

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Time one request and hand its metric to the recorder."""
        path = scope.get("path", "")
        if scope["type"] != "http" or not path.startswith("/api/") or path in self.SKIP_PATHS:
            await self.app(scope, receive, send)
            return

        timings = start_request()
        started = time.perf_counter()
        status = {"code": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            self._finish(scope, timings, status["code"], (time.perf_counter() - started) * 1000)

    def _finish(self, scope: Scope, timings: RequestTimings, status_code: int, duration_ms: float) -> None:
        recorder = self._recorder()
        if recorder is None:
            return
        method = scope.get("method", "GET")
        template = self._route_template(scope) or scope.get("path", "")
        activity = activity_for(method, template)
        try:
            recorder.record(
                RequestMetric(
                    recorded_at=datetime.now(UTC),
                    method=method,
                    route=template,
                    activity=activity.name,
                    status_code=status_code,
                    duration_ms=round(duration_ms, 2),
                    warehouse_ms=round(timings.warehouse_ms, 2),
                    warehouse_calls=timings.warehouse_calls,
                    lakebase_ms=round(timings.lakebase_ms, 2),
                    lakebase_calls=timings.lakebase_calls,
                    user_name=_forwarded_user(scope),
                )
            )
        except Exception:  # noqa: BLE001 - timing must never fail a request
            logger.debug("Could not record request metric", exc_info=True)


def _forwarded_user(scope: Scope) -> str | None:
    """Return the Databricks Apps forwarded email for the request, when present."""
    for key, value in scope.get("headers", []):
        if key == b"x-forwarded-email":
            return value.decode("latin-1") or None
    return None
