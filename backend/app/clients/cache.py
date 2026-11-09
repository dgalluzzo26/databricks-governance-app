from __future__ import annotations

import threading
from collections.abc import Callable, Hashable
from typing import TypeVar

from cachetools import TTLCache

T = TypeVar("T")


class TtlCache:
    """Thread-safe TTL cache with per-key loading so concurrent misses load once."""

    def __init__(self, ttl_seconds: int, maxsize: int = 1024) -> None:
        self._cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl_seconds)
        self._lock = threading.Lock()
        self._key_locks: dict[Hashable, threading.Lock] = {}

    def get_or_load(self, key: Hashable, loader: Callable[[], T]) -> T:
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
                self._cache[key] = value
                self._key_locks.pop(key, None)
            return value

    def invalidate(self, key: Hashable | None = None) -> None:
        with self._lock:
            if key is None:
                self._cache.clear()
            else:
                self._cache.pop(key, None)
