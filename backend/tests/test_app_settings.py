"""App-wide runtime settings: storage, legacy fallback, metadata-cache switch, and admin API."""

from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app.clients.cache import TtlCache
from backend.app.models.app_settings import AppSettings
from backend.app.repositories.memory import InMemoryStorageProvider
from backend.app.services.app_settings import SETTINGS_KEY, AppSettingsService
from backend.tests.conftest import headers_for

ADMIN = SimpleNamespace(user_name="admin@example.com")
ANALYST = headers_for("analyst@example.com")
ADMIN_HEADERS = headers_for("admin@example.com")


def _service(
    storage: InMemoryStorageProvider | None = None, cache: TtlCache | None = None
) -> AppSettingsService:
    return AppSettingsService(storage or InMemoryStorageProvider(), cache)


def test_defaults_when_nothing_is_stored() -> None:
    assert _service().get_settings() == AppSettings()


def test_save_persists_under_app_key_and_switches_cache() -> None:
    storage = InMemoryStorageProvider()
    cache = TtlCache(60)
    cache.get_or_refresh("seed", lambda: "stale", refresh_after=60)
    svc = _service(storage, cache)

    svc.save_settings(AppSettings(disable_metadata_cache=True), ADMIN)

    assert storage.settings.get_all()[SETTINGS_KEY] == {"disable_metadata_cache": True}
    assert cache.disabled is True
    assert cache.get_or_refresh("seed", lambda: "live", refresh_after=60) == "live"

    svc.save_settings(AppSettings(disable_metadata_cache=False), ADMIN)
    assert cache.disabled is False


def test_legacy_data_access_flag_is_read_when_app_key_missing() -> None:
    storage = InMemoryStorageProvider()
    storage.settings.put_many({"data_access": {"disable_metadata_cache": True}}, "tester")
    assert _service(storage).get_settings().disable_metadata_cache is True


def test_app_key_wins_over_legacy_flag() -> None:
    storage = InMemoryStorageProvider()
    storage.settings.put_many({"data_access": {"disable_metadata_cache": True}}, "tester")
    storage.settings.put_many({SETTINGS_KEY: {"disable_metadata_cache": False}}, "tester")
    assert _service(storage).get_settings().disable_metadata_cache is False


def test_invalid_stored_blob_falls_back_to_defaults() -> None:
    storage = InMemoryStorageProvider()
    storage.settings.put_many({SETTINGS_KEY: {"disable_metadata_cache": "maybe-not"}}, "tester")
    assert _service(storage).get_settings() == AppSettings()


def test_partial_save_keeps_stored_fields() -> None:
    svc = _service()
    svc.save_settings(AppSettings(disable_metadata_cache=True), ADMIN)
    svc.save_settings(AppSettings(), ADMIN)  # nothing set: stored value stays
    assert svc.get_settings().disable_metadata_cache is True


def test_metadata_cache_disabled_is_memoized_and_survives_read_failure() -> None:
    storage = InMemoryStorageProvider()
    svc = _service(storage)
    svc.save_settings(AppSettings(disable_metadata_cache=True), ADMIN)
    assert svc.metadata_cache_disabled() is True

    # Another instance flips the stored value; this instance keeps the memoized value briefly.
    storage.settings.put_many({SETTINGS_KEY: {"disable_metadata_cache": False}}, "other")
    assert svc.metadata_cache_disabled() is True

    # After the memo window the new value is read.
    svc._memo_loaded_at = 0.0  # noqa: SLF001 - force the memo to expire
    assert svc.metadata_cache_disabled() is False


def test_check_keeps_last_value_when_storage_read_fails() -> None:
    storage = InMemoryStorageProvider()
    svc = _service(storage)
    svc.save_settings(AppSettings(disable_metadata_cache=True), ADMIN)
    svc._memo_loaded_at = 0.0  # noqa: SLF001 - force a storage read on the next check

    def boom() -> dict:
        raise RuntimeError("lakebase down")

    original = storage.settings.get_all
    storage.settings.get_all = boom  # type: ignore[method-assign]
    try:
        assert svc.metadata_cache_disabled() is True
    finally:
        storage.settings.get_all = original  # type: ignore[method-assign]


def test_apply_at_startup_sets_cache_from_storage() -> None:
    storage = InMemoryStorageProvider()
    storage.settings.put_many({SETTINGS_KEY: {"disable_metadata_cache": True}}, "tester")
    cache = TtlCache(60)
    _service(storage, cache).apply()
    assert cache.disabled is True


def test_admin_api_reads_and_writes_settings(client: TestClient) -> None:
    current = client.get("/api/app-settings", headers=ADMIN_HEADERS)
    assert current.status_code == 200
    assert current.json()["disableMetadataCache"] is False

    saved = client.put("/api/app-settings", headers=ADMIN_HEADERS, json={"disableMetadataCache": True})
    assert saved.status_code == 200
    assert saved.json()["disableMetadataCache"] is True
    assert client.get("/api/app-settings", headers=ADMIN_HEADERS).json()["disableMetadataCache"] is True

    # Reset so the shared test container is not left with the cache disabled.
    client.put("/api/app-settings", headers=ADMIN_HEADERS, json={"disableMetadataCache": False})


def test_app_settings_are_admin_only(client: TestClient) -> None:
    assert client.get("/api/app-settings", headers=ANALYST).status_code == 403
    denied = client.put("/api/app-settings", headers=ANALYST, json={"disableMetadataCache": True})
    assert denied.status_code == 403
