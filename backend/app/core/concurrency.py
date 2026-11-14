"""Run independent blocking lookups concurrently within one request."""

from __future__ import annotations

import contextvars
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any


def run_parallel(*calls: Callable[[], Any]) -> list[Any]:
    """Run zero-argument callables concurrently and return their results in order.

    Each call runs in a copy of the caller's context, so per-request state such as
    dependency timings (``perf.track``) is attributed to the calling request. The first
    exception raised by any call propagates after all calls finish.

    Args:
        *calls: Callables to run.

    Returns:
        Results in the same order as ``calls``.
    """
    if len(calls) <= 1:
        return [call() for call in calls]
    results: list[Any] = []
    error: BaseException | None = None
    # A pool per call (not shared) so nested run_parallel calls can never starve each other.
    with ThreadPoolExecutor(max_workers=len(calls), thread_name_prefix="parallel") as pool:
        futures = [pool.submit(contextvars.copy_context().run, call) for call in calls]
        for future in futures:
            try:
                results.append(future.result())
            except BaseException as exc:  # noqa: BLE001 - re-raised below after all calls finish
                results.append(None)
                error = error or exc
    if error is not None:
        raise error
    return results
