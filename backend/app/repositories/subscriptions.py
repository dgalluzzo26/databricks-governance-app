"""Subscription store: interface and process-local implementation."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod

from backend.app.models.subscription import Subscription


class SubscriptionRepository(ABC):
    """Store for subscriptions; names are unique case-insensitively."""

    @abstractmethod
    def list_subscriptions(self) -> list[Subscription]:
        """List every subscription, newest first."""

    @abstractmethod
    def get_subscription(self, subscription_id: str) -> Subscription | None:
        """Fetch one subscription by id, or None."""

    @abstractmethod
    def get_by_name(self, name: str) -> Subscription | None:
        """Fetch one subscription by its unique name (case-insensitive), or None."""

    @abstractmethod
    def save_subscription(self, subscription: Subscription) -> Subscription:
        """Insert or replace a subscription keyed by ``subscription_id``."""

    @abstractmethod
    def delete_subscription(self, subscription_id: str) -> bool:
        """Delete a subscription; True when a row was removed."""


class InMemorySubscriptionRepository(SubscriptionRepository):
    """Process-local subscription store (memory and Delta backends, tests)."""

    def __init__(self) -> None:
        self._items: dict[str, Subscription] = {}
        self._lock = threading.Lock()

    def list_subscriptions(self) -> list[Subscription]:
        """List every subscription, newest first."""
        with self._lock:
            items = list(self._items.values())
        return sorted(items, key=lambda s: s.created_at, reverse=True)

    def get_subscription(self, subscription_id: str) -> Subscription | None:
        """Fetch one subscription by id, or None."""
        with self._lock:
            return self._items.get(subscription_id)

    def get_by_name(self, name: str) -> Subscription | None:
        """Fetch one subscription by its unique name (case-insensitive), or None."""
        needle = name.strip().lower()
        with self._lock:
            return next((s for s in self._items.values() if s.name.lower() == needle), None)

    def save_subscription(self, subscription: Subscription) -> Subscription:
        """Insert or replace a subscription keyed by ``subscription_id``."""
        with self._lock:
            self._items[subscription.subscription_id] = subscription
        return subscription

    def delete_subscription(self, subscription_id: str) -> bool:
        """Delete a subscription; True when a row was removed."""
        with self._lock:
            return self._items.pop(subscription_id, None) is not None
