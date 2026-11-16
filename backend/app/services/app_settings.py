"""App-wide runtime settings, stored under their own key in app settings storage.

The metadata-cache switch is read on every cached metadata lookup, so the stored value is
memoized for a few seconds. Sibling app instances pick up a change within that window
without a restart.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from pydantic import ValidationError

from backend.app.clients.cache import TtlCache
from backend.app.models.app_settings import AppSettings
from backend.app.models.user import CurrentUser
from backend.app.repositories.base import StorageProvider

logger = logging.getLogger(__name__)

SETTINGS_KEY = "app_runtime"
# Before this key existed the switch lived in the data-access settings blob; read it once as a fallback.
_LEGACY_DATA_ACCESS_KEY = "data_access"
_FLAG_MEMO_SECONDS = 5.0


class AppSettingsService:
    """Reads and writes app-wide settings and applies the metadata-cache switch.

    Args:
        storage: App state (settings JSON).
        metadata_cache: Shared UC metadata cache to switch on or off. None disables the switch.
    """

    def __init__(self, storage: StorageProvider, metadata_cache: TtlCache | None) -> None:
        self._storage = storage
        self._metadata_cache = metadata_cache
        self._lock = threading.Lock()
        self._memo_flag = False
        self._memo_loaded_at = 0.0

    def get_settings(self) -> AppSettings:
        """Return stored app settings, falling back to the legacy data-access field, then defaults."""
        all_settings = self._storage.settings.get_all()
        raw = all_settings.get(SETTINGS_KEY)
        if isinstance(raw, dict):
            try:
                return AppSettings.model_validate(raw)
            except ValidationError:
                logger.exception("Ignoring invalid app settings blob")
                return AppSettings()
        legacy = all_settings.get(_LEGACY_DATA_ACCESS_KEY)
        if isinstance(legacy, dict):
            return AppSettings(disable_metadata_cache=_legacy_flag(legacy))
        return AppSettings()

    def save_settings(
        self, body: AppSettings, actor: CurrentUser, *, exclude_unset: bool = True
    ) -> AppSettings:
        """Persist app settings and apply the metadata-cache switch on this instance.

        Args:
            body: Settings posted by an admin. Fields left out keep their stored values.
            actor: Admin performing the write.
            exclude_unset: When true, only fields present on ``body`` overwrite stored values.

        Returns:
            The stored settings.
        """
        stored = self.get_settings().model_dump(mode="json")
        incoming = body.model_dump(mode="json", exclude_unset=exclude_unset)
        settings = AppSettings.model_validate({**stored, **incoming})
        self._storage.settings.put_many({SETTINGS_KEY: settings.model_dump(mode="json")}, actor.user_name)
        self.apply(settings)
        logger.info(
            "App settings updated by %s (disable_metadata_cache=%s)",
            actor.user_name,
            settings.disable_metadata_cache,
        )
        return settings

    def apply(self, settings: AppSettings | None = None) -> None:
        """Apply the metadata-cache switch on this instance and refresh the memoized flag.

        Call once storage is open (startup). Save calls it automatically.

        Args:
            settings: Settings to apply; defaults to the stored settings.
        """
        cfg = settings or self.get_settings()
        with self._lock:
            self._memo_flag = cfg.disable_metadata_cache
            self._memo_loaded_at = time.monotonic()
        if self._metadata_cache is None:
            return
        self._metadata_cache.set_disabled_check(self.metadata_cache_disabled)
        self._metadata_cache.set_disabled(cfg.disable_metadata_cache)

    def metadata_cache_disabled(self) -> bool:
        """Whether the shared metadata cache should be bypassed.

        Memoized for a few seconds so cache reads do not each hit storage. On a read failure the
        last known value is kept (the cache stays enabled if it was never read).
        """
        now = time.monotonic()
        with self._lock:
            if now - self._memo_loaded_at < _FLAG_MEMO_SECONDS:
                return self._memo_flag
        try:
            flag = self.get_settings().disable_metadata_cache
        except Exception:  # noqa: BLE001 - never fail a metadata read because settings storage failed
            logger.warning("Could not read app settings; keeping last metadata cache setting", exc_info=True)
            with self._lock:
                self._memo_loaded_at = now
                return self._memo_flag
        with self._lock:
            self._memo_flag = flag
            self._memo_loaded_at = now
        return flag


def _legacy_flag(raw: dict[str, Any]) -> bool:
    """Read the legacy ``disable_metadata_cache`` flag from a data-access settings blob."""
    value = raw.get("disable_metadata_cache", raw.get("disableMetadataCache", False))
    return value is True
