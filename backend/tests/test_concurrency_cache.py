from __future__ import annotations

import threading
import time

import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient

from backend.app.clients.cache import TtlCache
from backend.app.core import perf
from backend.app.core.concurrency import run_parallel
from backend.app.main import _route_template


def _wait_until(predicate, timeout: float = 2.0) -> None:  # noqa: ANN001
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        time.sleep(0.01)


def test_get_or_refresh_serves_stale_value_while_reloading() -> None:
    cache = TtlCache(60)
    calls = {"n": 0}
    release = threading.Event()

    def loader() -> int:
        calls["n"] += 1
        if calls["n"] > 1:
            release.wait(2)
        return calls["n"]

    assert cache.get_or_refresh("k", loader, refresh_after=0) == 1
    # Stale: returns the old value at once and reloads on a background thread.
    assert cache.get_or_refresh("k", loader, refresh_after=0) == 1
    assert cache.get_or_refresh("k", loader, refresh_after=0) == 1
    release.set()
    _wait_until(lambda: cache.get_or_refresh("k", loader, refresh_after=60) == 2)
    assert calls["n"] == 2


def test_get_or_refresh_keeps_old_value_when_refresh_fails() -> None:
    cache = TtlCache(60)
    state = {"fail": False}

    def loader() -> str:
        if state["fail"]:
            raise RuntimeError("warehouse down")
        return "v1"

    assert cache.get_or_refresh("k", loader, refresh_after=0) == "v1"
    state["fail"] = True
    assert cache.get_or_refresh("k", loader, refresh_after=0) == "v1"
    _wait_until(lambda: "k" not in cache._refreshing)  # noqa: SLF001
    assert cache.get_or_refresh("k", loader, refresh_after=60) == "v1"


def test_get_or_refresh_first_load_error_is_not_cached_and_invalidate_reloads() -> None:
    cache = TtlCache(60)
    with pytest.raises(RuntimeError):
        cache.get_or_refresh("k", lambda: (_ for _ in ()).throw(RuntimeError("boom")), refresh_after=60)
    assert cache.get_or_refresh("k", lambda: "ok", refresh_after=60) == "ok"
    cache.invalidate("k")
    assert cache.get_or_refresh("k", lambda: "new", refresh_after=60) == "new"


def test_set_disabled_bypasses_load_and_refresh_and_clears_entries() -> None:
    cache = TtlCache(60)
    calls = {"n": 0}

    def loader() -> int:
        calls["n"] += 1
        return calls["n"]

    assert cache.get_or_refresh("k", loader, refresh_after=60) == 1
    assert cache.get_or_load("k2", loader) == 2
    cache.set_disabled(True)
    assert cache.disabled is True
    assert cache.get_or_refresh("k", loader, refresh_after=60) == 3
    assert cache.get_or_refresh("k", loader, refresh_after=60) == 4
    assert cache.get_or_load("k2", loader) == 5
    cache.set_disabled(False)
    assert cache.disabled is False
    assert cache.get_or_refresh("k", loader, refresh_after=60) == 6
    assert cache.get_or_refresh("k", loader, refresh_after=60) == 6


def test_disabled_check_bypasses_without_local_flag() -> None:
    cache = TtlCache(60)
    calls = {"n": 0}
    live = {"on": False}

    def loader() -> int:
        calls["n"] += 1
        return calls["n"]

    cache.set_disabled_check(lambda: live["on"])
    assert cache.get_or_refresh("k", loader, refresh_after=60) == 1
    assert cache.get_or_refresh("k", loader, refresh_after=60) == 1
    live["on"] = True
    assert cache.disabled is True
    assert cache.get_or_refresh("k", loader, refresh_after=60) == 2
    assert cache.get_or_refresh("k", loader, refresh_after=60) == 3


def test_run_parallel_keeps_order_and_request_timings() -> None:
    timings = perf.start_request()

    def query(value: int) -> int:
        with perf.track("warehouse"):
            time.sleep(0.05)
        return value

    started = time.perf_counter()
    assert run_parallel(lambda: query(1), lambda: query(2), lambda: query(3)) == [1, 2, 3]
    assert time.perf_counter() - started < 0.14
    assert timings.warehouse_calls == 3


def test_run_parallel_raises_after_all_calls_finish() -> None:
    finished: list[str] = []

    def slow() -> None:
        time.sleep(0.05)
        finished.append("slow")

    def bad() -> None:
        raise ValueError("nope")

    with pytest.raises(ValueError, match="nope"):
        run_parallel(bad, slow)
    assert finished == ["slow"]


def test_route_template_resolves_path_parameter_routes() -> None:
    router = APIRouter(prefix="/policies")

    @router.get("/{policy_id}")
    def get_policy(policy_id: str) -> dict:  # noqa: ARG001
        return {}

    api = APIRouter(prefix="/api")
    api.include_router(router)
    app = FastAPI()
    app.include_router(api)
    seen: dict[str, str | None] = {}

    @app.middleware("http")
    async def capture(request: Request, call_next):  # noqa: ANN001, ANN202
        response = await call_next(request)
        seen["template"] = _route_template(app, request.scope)
        return response

    TestClient(app).get("/api/policies/abc-123")
    assert seen["template"] == "/api/policies/{policy_id}"
