from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any

import pytest

from backend.app.models.data_access import (
    DataAccessRun,
    DataAccessSettings,
    DataAccessSettingsView,
    SyncRunStatus,
    SyncTrigger,
)
from backend.jobs import run_data_access_sync as job


@pytest.fixture(autouse=True)
def _restore_environ() -> Any:
    """The job helpers export PG*/DATABRICKS_* into os.environ; undo that after each test."""
    snapshot = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(snapshot)


class _FakeDataAccess:
    def __init__(
        self, *, settings: DataAccessSettings | None = None, run: DataAccessRun | None = None
    ) -> None:
        self.settings = settings or DataAccessSettings()
        self.run = run
        self.calls: list[dict[str, Any]] = []

    def get_settings(self) -> DataAccessSettingsView:
        return DataAccessSettingsView(settings=self.settings, apply_available=True, enrolled_catalogs=[])

    def run_sync(self, **kwargs):  # noqa: ANN003, ANN201
        self.calls.append(kwargs)
        if self.run is not None:
            return self.run
        return DataAccessRun(
            run_id=kwargs.get("run_id") or "r1",
            status=SyncRunStatus.SUCCESS,
            trigger=kwargs["trigger"],
            mode=kwargs.get("mode") or "dry_run",
            started_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            finished_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
            triggered_by=kwargs["actor"],
        )


class _FakeContainer:
    def __init__(self, data_access: _FakeDataAccess) -> None:
        self.data_access = data_access
        self.started = False
        self.stopped = False

    def startup(self, **kwargs: Any) -> None:
        self.started = True
        self.startup_kwargs = kwargs

    def shutdown(self) -> None:
        self.stopped = True


def test_parse_args_defaults() -> None:
    args = job._parse_args([])  # noqa: SLF001
    assert args.mode == ""
    assert args.trigger == "schedule"
    assert args.actor == "scheduler"
    assert args.run_id == ""


def _patch_runtime(monkeypatch: pytest.MonkeyPatch, container: _FakeContainer) -> None:
    def _config() -> SimpleNamespace:
        return SimpleNamespace()

    _config.cache_clear = lambda: None  # type: ignore[attr-defined]
    monkeypatch.setattr(job, "ensure_lakebase_env", lambda: None)
    monkeypatch.setattr(job, "hydrate_admin_secrets", lambda: [])
    monkeypatch.setattr(job, "get_config", _config)
    monkeypatch.setattr(job.ServiceContainer, "build", classmethod(lambda cls, cfg: container))


def test_main_noops_when_schedule_paused(monkeypatch: pytest.MonkeyPatch) -> None:
    data_access = _FakeDataAccess(settings=DataAccessSettings(schedule_enabled=False))
    container = _FakeContainer(data_access)
    _patch_runtime(monkeypatch, container)
    assert job.main(["--trigger=schedule"]) == 0
    assert data_access.calls == []
    assert container.started and container.stopped
    assert container.startup_kwargs == {
        "migrate_storage": False,
        "sync_schedule": False,
        "mirror_uc": False,
    }


def test_main_uses_settings_mode_and_returns_failure_status(monkeypatch: pytest.MonkeyPatch) -> None:
    failed = DataAccessRun(
        run_id="bad",
        status=SyncRunStatus.FAILED,
        trigger=SyncTrigger.SCHEDULE,
        mode="apply",
        started_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        finished_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        triggered_by="scheduler",
        error="boom",
    )
    data_access = _FakeDataAccess(
        settings=DataAccessSettings(schedule_mode="apply"),
        run=failed,
    )
    container = _FakeContainer(data_access)
    _patch_runtime(monkeypatch, container)
    assert job.main(["--trigger=schedule", "--run_id=bad"]) == 1
    assert data_access.calls[0]["mode"] == "apply"
    assert data_access.calls[0]["run_id"] == "bad"


def test_main_rejects_bad_trigger() -> None:
    assert job.main(["--trigger=cron"]) == 1


def test_ensure_lakebase_env_is_noop_when_already_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PGHOST", "db.example")
    monkeypatch.setenv("PGDATABASE", "governance")
    monkeypatch.setenv("PGUSER", "sp")
    monkeypatch.setenv("LAKEBASE_ENDPOINT", "projects/x/branches/production/endpoints/y")
    job.ensure_lakebase_env()


def test_ensure_lakebase_env_resolves_from_postgres_api(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("PGHOST", "PGDATABASE", "PGUSER", "LAKEBASE_ENDPOINT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LAKEBASE_PROJECT_ID", "data-governance-app")
    monkeypatch.setenv("LAKEBASE_DATABASE_ID", "governance")

    endpoint = SimpleNamespace(
        name="projects/data-governance-app/branches/production/endpoints/ep1",
        status=SimpleNamespace(hosts=SimpleNamespace(host="db.cloud.databricks.com")),
    )
    database = SimpleNamespace(status=SimpleNamespace(postgres_database="governance"))
    client = SimpleNamespace(
        postgres=SimpleNamespace(
            list_endpoints=lambda parent: [endpoint],
            get_database=lambda name: database,
        ),
        current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name="app-sp")),
    )
    monkeypatch.setattr(job, "WorkspaceClient", lambda: client)
    job.ensure_lakebase_env()
    assert job.os.environ["PGHOST"] == "db.cloud.databricks.com"
    assert job.os.environ["PGDATABASE"] == "governance"
    assert job.os.environ["PGUSER"] == "app-sp"
    assert job.os.environ["LAKEBASE_ENDPOINT"].endswith("/endpoints/ep1")
