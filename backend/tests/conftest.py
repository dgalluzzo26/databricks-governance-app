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

    def list(self, filter: str, attributes: str, count: int | None = None):  # noqa: A002
        del count
        # Prefer an exact userName eq "..." clause when present; otherwise substring match.
        quoted = [part for part in filter.split('"') if part and " " not in part and "=" not in part]
        needle = quoted[0] if quoted else ""
        matches = []
        for user_name, groups in self._groups.items():
            if needle.lower() in user_name.lower() or ("eq" in filter and needle == user_name):
                matches.append(
                    SimpleNamespace(
                        id="123",
                        user_name=user_name,
                        display_name="Test User",
                        emails=[SimpleNamespace(value=user_name)],
                        groups=[SimpleNamespace(display=g) for g in groups],
                    )
                )
        return iter(matches)


class FakeWorkspaceFactory:
    host = HOST
    admin_configured = False

    def __init__(self, groups_by_user: dict[str, list[str]], *, admin_configured: bool = False) -> None:
        self.admin_configured = admin_configured
        self._client = SimpleNamespace(
            users=FakeUsersApi(groups_by_user),
            groups=SimpleNamespace(
                list=lambda **_kwargs: iter([SimpleNamespace(id="g1", display_name="data-stewards")])
            ),
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
    container.performance._workspace = fake
    return container


@pytest.fixture
def client(config: AppConfig, container: ServiceContainer) -> TestClient:
    with TestClient(create_app(config, container)) as test_client:
        yield test_client


def headers_for(email: str, display_name: str = "Header Name") -> dict[str, str]:
    return {"X-Forwarded-Email": email, "X-Forwarded-Preferred-Username": display_name}
