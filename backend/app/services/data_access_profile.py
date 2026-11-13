"""Per-run timing for data-access syncs.

A ``RunProfiler`` is bound to the thread running ``run_sync``. Loaders and apply helpers call
the module-level ``phase`` / ``timed_call`` / ``record_size`` / ``note`` helpers, which are
no-ops on threads without a profiler (for example approval-time membership changes).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

from backend.app.models.data_access import DataAccessRunProfile

_local = threading.local()


class RunProfiler:
    """Accumulates phase timings, call counts, and sizes for one sync run."""

    def __init__(self) -> None:
        self.phases: dict[str, float] = {}
        self.calls: dict[str, int] = {}
        self.call_seconds: dict[str, float] = {}
        self.call_max_seconds: dict[str, float] = {}
        self.sizes: dict[str, int] = {}
        self.notes: list[str] = []

    def add_phase(self, name: str, seconds: float) -> None:
        """Add ``seconds`` to phase ``name`` (phases may repeat)."""
        self.phases[name] = self.phases.get(name, 0.0) + seconds

    def add_call(self, name: str, seconds: float) -> None:
        """Count one external call of kind ``name`` that took ``seconds``."""
        self.calls[name] = self.calls.get(name, 0) + 1
        self.call_seconds[name] = self.call_seconds.get(name, 0.0) + seconds
        self.call_max_seconds[name] = max(self.call_max_seconds.get(name, 0.0), seconds)

    def snapshot(self) -> DataAccessRunProfile:
        """Rounded copy suitable for storing on the run."""
        return DataAccessRunProfile(
            phases={k: round(v, 3) for k, v in self.phases.items()},
            calls=dict(self.calls),
            call_seconds={k: round(v, 3) for k, v in self.call_seconds.items()},
            call_max_seconds={k: round(v, 3) for k, v in self.call_max_seconds.items()},
            sizes=dict(self.sizes),
            notes=list(self.notes),
        )


def record_call(name: str, seconds: float) -> None:
    """Record one external call measured elsewhere (for example on a worker thread)."""
    profiler = active()
    if profiler is not None:
        profiler.add_call(name, seconds)


def active() -> RunProfiler | None:
    """The profiler bound to the current thread, if any."""
    return getattr(_local, "profiler", None)


@contextmanager
def profiling(profiler: RunProfiler) -> Iterator[RunProfiler]:
    """Bind ``profiler`` to the current thread for the duration of the block."""
    previous = active()
    _local.profiler = profiler
    try:
        yield profiler
    finally:
        _local.profiler = previous


@contextmanager
def phase(name: str) -> Iterator[None]:
    """Time a block as phase ``name``."""
    profiler = active()
    if profiler is None:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        profiler.add_phase(name, time.perf_counter() - started)


@contextmanager
def timed_call(name: str) -> Iterator[None]:
    """Time one external call (SQL statement, SCIM request) of kind ``name``."""
    profiler = active()
    if profiler is None:
        yield
        return
    started = time.perf_counter()
    try:
        yield
    finally:
        profiler.add_call(name, time.perf_counter() - started)


_log = logging.getLogger("backend.app.services.data_access_progress")


class Progress:
    """Logs ``done of total`` with elapsed time and an ETA roughly every 5% of the work.

    Use for long loops in a sync run (grants, membership) so the Job log shows where it is.
    """

    def __init__(self, label: str, total: int) -> None:
        self.label = label
        self.total = total
        self.done = 0
        self._started = time.perf_counter()
        self._step = max(1, total // 20)

    def tick(self, n: int = 1) -> None:
        """Count ``n`` finished items and log when a progress milestone is crossed."""
        before = self.done
        self.done += n
        if self.done == self.total or self.done // self._step != before // self._step:
            elapsed = time.perf_counter() - self._started
            rate = self.done / elapsed if elapsed > 0 else 0.0
            eta = (self.total - self.done) / rate if rate > 0 else 0.0
            _log.info(
                "progress %s %d of %d (%d%%) elapsed=%.0fs eta=%.0fs",
                self.label,
                self.done,
                self.total,
                round(100 * self.done / self.total) if self.total else 100,
                elapsed,
                eta,
            )


def record_size(name: str, value: int) -> None:
    """Record a row or item count."""
    profiler = active()
    if profiler is not None:
        profiler.sizes[name] = int(value)


def note(message: str) -> None:
    """Record a warning to read alongside the numbers."""
    profiler = active()
    if profiler is not None and message not in profiler.notes:
        profiler.notes.append(message)
