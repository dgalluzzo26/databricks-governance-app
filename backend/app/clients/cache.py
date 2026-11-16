"""In-process TTL cache used for Unity Catalog metadata and identity lookups."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Hashable
from typing import Any, TypeVar

from cachetools import TTLCache

T = TypeVar("T")

logger = logging.getLogger(__name__)


class TtlCache:
    """Thread-safe TTL cache with per-key loading so concurrent misses load once.

    The cache is local to the process; entries are evicted after ``ttl_seconds`` or when
    ``maxsize`` is exceeded (least recently used first).

    Args:
        ttl_seconds: Lifetime of each entry in seconds.
        maxsize: Maximum number of entries kept.
    """

    def __init__(self, ttl_seconds: int, maxsize: int = 1024) -> None:
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl_seconds)
        self._lock = threading.Lock()
        self._key_locks: dict[Hashable, threading.Lock] = {}
        self._swr: dict[Hashable, tuple[Any, float]] = {}
        self._refreshing: set[Hashable] = set()
        self._disabled = False
        self._disabled_check: Callable[[], bool] | None = None

    @property
    def disabled(self) -> bool:
        """True when reads always call the loader and never store results."""
        return self._is_disabled()

    def set_disabled_check(self, check: Callable[[], bool] | None) -> None:
        """Optional callback consulted on each read (for a setting shared across app instances).

        The callback runs before the cache lock is taken, so it may do I/O. It should be cheap
        (memoized) because it runs on every read.

        Args:
            check: Returns true to bypass the cache; None clears the callback.
        """
        with self._lock:
            self._disabled_check = check

    def set_disabled(self, disabled: bool) -> None:
        """Enable or disable caching; clears stored entries on every call.

        Args:
            disabled: When true, :meth:`get_or_load` and :meth:`get_or_refresh` always load.
        """
        with self._lock:
            self._disabled = bool(disabled)
            self._cache.clear()
            self._swr.clear()

    def _is_disabled(self) -> bool:
        """True when the local flag or the optional check says to bypass the cache."""
        if self._disabled:
            return True
        check = self._disabled_check
        if check is None:
            return False
        try:
            return bool(check())
        except Exception:  # noqa: BLE001 - a failed check must never fail a metadata read
            logger.warning("Metadata cache disabled check failed; keeping cache enabled", exc_info=True)
            return False

    def get_or_load(self, key: Hashable, loader: Callable[[], T]) -> T:
        """Return the cached value for ``key``, calling ``loader`` once on a miss.

        Concurrent callers missing the same key wait for a single load; other keys are not
        blocked while it runs. Exceptions from ``loader`` propagate and nothing is cached.
        When the cache is disabled, ``loader`` always runs and nothing is stored.

        Args:
            key: Cache key.
            loader: Zero-argument callable producing the value.

        Returns:
            The cached or freshly loaded value.
        """
        if self._is_disabled():
            return loader()
        with self._lock:
            if key in self._cache:
                return self._cache[key]
            key_lock = self._key_locks.setdefault(key, threading.Lock())
        with key_lock:
            with self._lock:
                if key in self._cache:
                    return self._cache[key]
            value = loader()
            with self._lock:
                if not self._disabled:
                    self._cache[key] = value
                self._key_locks.pop(key, None)
            return value

    def get_or_refresh(self, key: Hashable, loader: Callable[[], T], *, refresh_after: float) -> T:
        """Return ``key``'s value, refreshing it in the background once it is older than ``refresh_after``.

        Stale-while-revalidate: only the first load blocks. After that, callers get the last
        value immediately and a single daemon thread reloads it. A failed refresh keeps the
        old value. Entries here are separate from :meth:`get_or_load` and never expire on
        their own; :meth:`invalidate` drops them. When the cache is disabled, ``loader``
        always runs and nothing is stored.

        Args:
            key: Cache key.
            loader: Zero-argument callable producing the value.
            refresh_after: Age in seconds after which the value is reloaded in the background.

        Returns:
            The cached (possibly stale) or freshly loaded value.
        """
        if self._is_disabled():
            return loader()
        with self._lock:
            entry = self._swr.get(key)
            if entry is not None:
                value, loaded_at = entry
                if time.monotonic() - loaded_at >= refresh_after and key not in self._refreshing:
                    self._refreshing.add(key)
                    threading.Thread(
                        target=self._refresh, args=(key, loader), name="cache-refresh", daemon=True
                    ).start()
                return value
            key_lock = self._key_locks.setdefault(("swr", key), threading.Lock())
        with key_lock:
            with self._lock:
                entry = self._swr.get(key)
                if entry is not None:
                    return entry[0]
            value = loader()
            with self._lock:
                if not self._disabled:
                    self._swr[key] = (value, time.monotonic())
                self._key_locks.pop(("swr", key), None)
            return value

    def _refresh(self, key: Hashable, loader: Callable[[], object]) -> None:
        try:
            value = loader()
        except Exception as exc:  # noqa: BLE001 - keep serving the stale value
            logger.warning("Background cache refresh failed for %s: %s", key, exc)
            value = None
            failed = True
        else:
            failed = False
        with self._lock:
            self._refreshing.discard(key)
            if not failed and key in self._swr:
                self._swr[key] = (value, time.monotonic())

    def invalidate(self, key: Hashable | None = None) -> None:
        """Drop one entry, or every entry when ``key`` is None.

        Args:
            key: Cache key to remove; None clears the whole cache.
        """
        with self._lock:
            if key is None:
                self._cache.clear()
                self._swr.clear()
            else:
                self._cache.pop(key, None)
                self._swr.pop(key, None)
