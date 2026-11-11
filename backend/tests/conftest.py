from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import AppConfig, StorageBackend
from backend.app.core.container import ServiceContainer
from backend.app.main import create_app
from backend.app.repositories.memory import InMemoryStorageProvider

HOST = "https://example.cloud.databricks.com"


@pytest.fixture(autouse=True)
def _skip_admin_secret_hydrate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must not call the workspace Secrets API (can hang on auth)."""
    monkeypatch.setattr("backend.app.core.container.hydrate_admin_secrets", lambda **_kwargs: [])
    monkeypatch.setattr("backend.app.main.hydrate_admin_secrets", lambda **_kwargs: [])


class FakeUsersApi:
    def __init__(self, groups_by_user: dict[str, list[str]]) -> None:
        self._groups = groups_by_user

    def list(self, filter: str, attributes: str):  # noqa: A002 - mirrors the SDK signature
        user_name = filter.split('"')[1]
        if user_name not in self._groups:
            return iter([])
        groups = [SimpleNamespace(display=g) for g in self._groups[user_name]]
        return iter([SimpleNamespace(id="123", display_name="Test User", groups=groups)])


class FakeWorkspaceFactory:
    host = HOST
    admin_configured = False

    def __init__(self, groups_by_user: dict[str, list[str]], *, admin_configured: bool = False) -> None:
        self.admin_configured = admin_configured
        self._client = SimpleNamespace(
            users=FakeUsersApi(groups_by_user),
            groups=SimpleNamespace(list=lambda **_kwargs: iter([])),
            current_user=SimpleNamespace(me=lambda: SimpleNamespace(user_name="local@example.com")),
            config=SimpleNamespace(host=HOST),
        )

    def app(self):
        return self._client

    def admin(self):
        return self._client

    def service_principal(self):
        return self.admin()

    def invalidate_admin(self) -> None:
        return None

    def for_user(self, access_token: str):
        groups = [SimpleNamespace(display=g) for g in ("admins", "data-stewards")]
        me = SimpleNamespace(id="999", display_name="Token User", groups=groups)
        return SimpleNamespace(current_user=SimpleNamespace(me=lambda: me))


@pytest.fixture
def config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        storage_backend=StorageBackend.MEMORY,
        admin_group="admins",
        frontend_dist=tmp_path / "missing-dist",
        _env_file=None,
    )


@pytest.fixture
def container(config: AppConfig) -> ServiceContainer:
    container = ServiceContainer.build(config, storage=InMemoryStorageProvider())
    fake = FakeWorkspaceFactory(
        {"admin@example.com": ["admins", "users"], "analyst@example.com": ["users"]},
        admin_configured=True,
    )
    container.workspace = fake
    container.identity._factory = fake
    container.request_context._factory = fake
    container.app_info._factory = fake
    return container


@pytest.fixture
def client(config: AppConfig, container: ServiceContainer) -> TestClient:
    with TestClient(create_app(config, container)) as test_client:
        yield test_client


def headers_for(email: str, display_name: str = "Header Name") -> dict[str, str]:
    return {"X-Forwarded-Email": email, "X-Forwarded-Preferred-Username": display_name}
