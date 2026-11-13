"""Per-run timing profile for data-access syncs."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from backend.app.models.data_access import DataAccessReadiness, PlannedGrant, SyncTrigger
from backend.app.services import data_access_profile as prof
from backend.app.services.data_access_planner import AssignmentRow
from backend.tests.test_data_access_pending import _loaders, _reviewed, _Scim, _service


def test_helpers_are_noops_without_a_profiler() -> None:
    with prof.phase("x"), prof.timed_call("y"):
        prof.record_size("z", 1)
        prof.note("n")
    assert prof.active() is None


def test_profiler_accumulates_phases_calls_sizes_and_unique_notes() -> None:
    profiler = prof.RunProfiler()
    with prof.profiling(profiler):
        for _ in range(2):
            with prof.phase("load"), prof.timed_call("sql.read"):
                pass
        prof.record_size("rows", 7)
        prof.note("truncated")
        prof.note("truncated")
    assert prof.active() is None
    snap = profiler.snapshot()
    assert snap.calls == {"sql.read": 2}
    assert set(snap.phases) == {"load"} and snap.call_max_seconds["sql.read"] >= 0
    assert snap.sizes == {"rows": 7} and snap.notes == ["truncated"]


def test_profiling_restores_the_previous_profiler() -> None:
    outer, inner = prof.RunProfiler(), prof.RunProfiler()
    with prof.profiling(outer):
        with prof.profiling(inner):
            assert prof.active() is inner
        assert prof.active() is outer


def test_run_sync_stores_profile_with_preflight_phases_and_sizes(caplog: pytest.LogCaptureFixture) -> None:
    svc = _service()
    _loaders(
        svc,
        tags=_reviewed("cat", "fin", "budget", "finance.budget"),
        assignments=[AssignmentRow("ana@example.com", "USER", "finance.budget")],
    )
    with caplog.at_level(logging.INFO, logger="backend.app.services.data_access"):
        run = svc.run_sync(actor="admin", trigger=SyncTrigger.USER, preflight={"job.container_startup": 4.2})
    profile = run.profile
    assert profile is not None
    assert profile.phases["job.container_startup"] == 4.2
    for name in ("load.tags", "load.grants", "load.members", "plan", "record_state", "write_run"):
        assert name in profile.phases
    assert profile.sizes["table_tag_rows"] == 2 and profile.sizes["plan_grants_to_add"] == 3
    assert profile.sizes["assignments_read"] == 1 and profile.sizes["gap_items"] == 5
    assert svc.get_run(run.run_id).profile is not None
    assert any("data_access_profile mode=dry_run" in r.message for r in caplog.records)


def test_failed_run_keeps_its_profile() -> None:
    svc = _service()
    _loaders(svc)

    def _boom(_settings):  # noqa: ANN001, ANN202
        raise RuntimeError("warehouse down")

    svc._grant_loader = _boom  # noqa: SLF001
    run = svc.run_sync(actor="admin", trigger=SyncTrigger.USER)
    assert run.status.value == "failed"
    assert run.profile is not None and "load.tags" in run.profile.phases


def test_apply_profile_counts_scim_and_grant_calls() -> None:
    scim = _Scim({"dac_finance_budget": "g1"})
    svc = _service(scim)
    _loaders(
        svc,
        tags=_reviewed("cat", "fin", "budget", "finance.budget"),
        groups={"dac_finance_budget"},
        assignments=[AssignmentRow("ana@example.com", "USER", "finance.budget")],
    )
    svc.readiness = lambda: DataAccessReadiness(  # type: ignore[method-assign]
        dry_run_ready=True, apply_ready=True, checks=[], setup_checks=[], setup_ready=True
    )
    run = svc.run_sync(actor="admin", trigger=SyncTrigger.USER, mode="apply")
    profile = run.profile
    assert profile is not None
    for name in ("apply", "apply.groups", "apply.nest", "apply.membership", "apply.grants"):
        assert name in profile.phases
    assert profile.calls["sql.grant_table"] == 1 and profile.calls["sql.grant_catalog"] == 1
    assert profile.calls["scim.resolve_user"] == 1 and profile.calls["scim.patch_group"] >= 1
    assert profile.sizes["apply_failures"] == 0


def test_truncated_scim_group_page_is_noted() -> None:
    class _Paged(_Scim):
        def do(self, method, path, query=None, body=None):  # noqa: ANN001, ANN201
            del path, query, body
            assert method == "GET"
            return {"totalResults": 150, "Resources": [{"id": "g1", "displayName": "dac_a"}]}

    svc = _service(_Paged())
    profiler = prof.RunProfiler()
    with prof.profiling(profiler):
        groups = svc._account_scim_list_groups()  # noqa: SLF001
    assert len(groups) == 1
    assert profiler.notes and "returned 1 of 150" in profiler.notes[0]
    assert profiler.calls == {"scim.list_groups": 1}


def test_revoke_calls_are_counted_by_securable_type() -> None:
    svc = _service()
    svc._sql = SimpleNamespace(execute=lambda _sql: [])  # noqa: SLF001
    grant = PlannedGrant(
        securable_type="schema", full_name="cat.fin", principal="dac_finance", privilege="USE_SCHEMA"
    )
    profiler = prof.RunProfiler()
    with prof.profiling(profiler):
        svc._apply_grants([], [grant], [], {})  # noqa: SLF001
    assert profiler.calls == {"sql.revoke_schema": 1}


def test_progress_logs_milestones_and_finishes(caplog: pytest.LogCaptureFixture) -> None:
    progress = prof.Progress("grants", 40)
    with caplog.at_level(logging.INFO, logger="backend.app.services.data_access_progress"):
        for _ in range(40):
            progress.tick()
    messages = [r.message for r in caplog.records]
    assert progress.done == 40
    assert any("progress grants 2 of 40 (5%)" in m for m in messages)
    assert messages[-1].startswith("progress grants 40 of 40 (100%)")
    assert len(messages) <= 21


def test_progress_handles_empty_totals(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="backend.app.services.data_access_progress"):
        prof.Progress("none", 0).tick()
    assert any("progress none 1 of 0 (100%)" in r.message for r in caplog.records)
