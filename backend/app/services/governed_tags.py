from __future__ import annotations

import logging

from databricks.sdk.errors import NotFound
from databricks.sdk.service.tags import TagPolicy, Value

from backend.app.clients.cache import TtlCache
from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.errors import InvalidRequestError, UpstreamError
from backend.app.models.identity_attribute import GovernedTagList, GovernedTagSummary

logger = logging.getLogger(__name__)


class GovernedTagService:
    """Reads Unity Catalog governed tag policies for identity attribute vocabulary."""

    def __init__(self, factory: WorkspaceClientFactory, cache: TtlCache) -> None:
        self._factory = factory
        self._cache = cache

    def list_tags(self, *, q: str | None = None, limit: int = 50) -> GovernedTagList:
        tags = self._all_tags()
        query = (q or "").strip().lower()
        if query:
            tags = [
                tag
                for tag in tags
                if query in tag.tag_key.lower()
                or query in (tag.description or "").lower()
                or any(query in value.lower() for value in tag.values)
            ]
        limit = max(1, min(limit, 200))
        truncated = len(tags) > limit
        return GovernedTagList(tags=tags[:limit], total=len(tags), truncated=truncated)

    def get_tag(self, tag_key: str) -> GovernedTagSummary:
        key = tag_key.strip()
        cached = next((tag for tag in self._all_tags() if tag.tag_key == key), None)
        if cached is not None:
            return cached
        try:
            policy = self._factory.admin().tag_policies.get_tag_policy(tag_key=key)
        except NotFound as exc:
            raise InvalidRequestError(f"Governed tag '{key}' was not found") from exc
        except Exception as exc:  # noqa: BLE001
            raise UpstreamError(f"Could not read governed tag '{key}': {exc}") from exc
        return self._to_summary(policy)

    def values_for_keys(self, tag_keys: list[str]) -> list[str]:
        values: list[str] = []
        seen: set[str] = set()
        for key in tag_keys:
            for value in self.get_tag(key).values:
                if value in seen:
                    continue
                seen.add(value)
                values.append(value)
        return values

    def require_tags(self, tag_keys: list[str]) -> list[GovernedTagSummary]:
        return [self.get_tag(key) for key in tag_keys]

    def ensure_values(self, tag_key: str, values: list[str]) -> GovernedTagSummary:
        """Add missing values to a governed tag, keeping every existing value."""
        current = self.get_tag(tag_key)
        missing = [value for value in values if value not in current.values]
        if not missing:
            return current
        merged = [*current.values, *missing]
        try:
            policy = self._factory.admin().tag_policies.update_tag_policy(
                tag_key=current.tag_key,
                tag_policy=TagPolicy(
                    tag_key=current.tag_key, values=[Value(name=value) for value in merged]
                ),
                update_mask="values",
            )
        except Exception as exc:  # noqa: BLE001
            raise UpstreamError(f"Could not update governed tag '{tag_key}': {exc}") from exc
        self._cache.invalidate(("governed-tag-policies",))
        logger.info("Added values %s to governed tag %s", missing, tag_key)
        return self._to_summary(policy)

    def _all_tags(self) -> list[GovernedTagSummary]:
        return self._cache.get_or_load(("governed-tag-policies",), self._load_all)

    def _load_all(self) -> list[GovernedTagSummary]:
        try:
            policies = list(self._factory.admin().tag_policies.list_tag_policies())
        except Exception as exc:  # noqa: BLE001
            raise UpstreamError(f"Could not list governed tags: {exc}") from exc
        tags = [self._to_summary(policy) for policy in policies]
        tags.sort(key=lambda tag: tag.tag_key.lower())
        logger.info("Loaded %s governed tag policies", len(tags))
        return tags

    @staticmethod
    def _to_summary(policy: object) -> GovernedTagSummary:
        tag_key = str(getattr(policy, "tag_key", "") or "")
        description = getattr(policy, "description", None) or None
        values = [
            str(getattr(value, "name", "") or "")
            for value in (getattr(policy, "values", None) or [])
            if getattr(value, "name", None)
        ]
        return GovernedTagSummary(
            tag_key=tag_key,
            description=description,
            values=values,
            value_count=len(values),
        )
