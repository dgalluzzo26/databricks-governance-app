"""Unity Catalog governed tag policies as identity attribute vocabularies.

Governed tag policies define the allowed values for tags such as ``sensitivity_category``,
``sensitivity``, ``mask_rule``, and ``row_filter``. This service lists and reads them through
the admin workspace client (cached), and appends new values when the app registers mask rules
or row-filter dimensions so tag assignments and ABAC policies stay consistent.
"""

from __future__ import annotations

import logging
import time

from databricks.sdk.errors import NotFound
from databricks.sdk.service.tags import TagPolicy, Value

from backend.app.clients.cache import TtlCache
from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.errors import InvalidRequestError, UpstreamError
from backend.app.models.identity_attribute import GovernedTagList, GovernedTagSummary

logger = logging.getLogger(__name__)

# Unity Catalog can take a moment to accept a newly added tag value in ABAC MATCH clauses.
_TAG_VALUE_WAIT_SECONDS = 8.0
_TAG_VALUE_POLL_SECONDS = 0.4


class GovernedTagService:
    """Reads Unity Catalog governed tag policies for identity attribute vocabulary.

    Args:
        factory: Workspace client factory; tag policies are read and updated as the admin SP.
        cache: TTL cache holding the full tag policy list.
    """

    def __init__(self, factory: WorkspaceClientFactory, cache: TtlCache) -> None:
        self._factory = factory
        self._cache = cache

    def list_tags(self, *, q: str | None = None, limit: int = 50) -> GovernedTagList:
        """List governed tags, optionally filtered by a search string.

        Args:
            q: Case-insensitive substring matched against tag key, description, and values.
            limit: Maximum tags to return, clamped to 1-200.

        Returns:
            Matching tags sorted by key, the total match count, and a ``truncated`` flag.

        Raises:
            UpstreamError: If the tag policies cannot be listed.
        """
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
        """Return one governed tag, preferring the cached list and falling back to a direct read.

        Args:
            tag_key: Exact tag key (surrounding whitespace is ignored).

        Returns:
            The tag's key, description, and allowed values.

        Raises:
            InvalidRequestError: If no governed tag policy exists for the key.
            UpstreamError: If the tag policies cannot be read.
        """
        key = tag_key.strip()
        cached = next((tag for tag in self._all_tags() if tag.tag_key == key), None)
        if cached is not None:
            return cached
        return self._fetch_tag(key)

    def values_for_keys(self, tag_keys: list[str]) -> list[str]:
        """Return the union of allowed values across tags, de-duplicated in first-seen order.

        Args:
            tag_keys: Governed tag keys to combine.

        Returns:
            The combined value list.

        Raises:
            InvalidRequestError: If any tag does not exist.
            UpstreamError: If the tag policies cannot be read.
        """
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
        """Resolve every tag key, failing if any is missing.

        Args:
            tag_keys: Governed tag keys that must exist.

        Returns:
            The tags in the same order as ``tag_keys``.

        Raises:
            InvalidRequestError: If any tag does not exist.
            UpstreamError: If the tag policies cannot be read.
        """
        return [self.get_tag(key) for key in tag_keys]

    def ensure_values(self, tag_key: str, values: list[str]) -> GovernedTagSummary:
        """Add missing values to a governed tag, keeping every existing value.

        No-op when every value is already present. Otherwise updates the tag policy in
        Unity Catalog as the admin SP and invalidates the cached tag list.

        Args:
            tag_key: Governed tag key to extend.
            values: Values that must be allowed (exact, case-sensitive match).

        Returns:
            The tag after the update (or unchanged).

        Raises:
            InvalidRequestError: If the tag does not exist.
            UpstreamError: If the tag policy cannot be read or updated.
        """
        wanted = [value for value in values if value]
        current = self._fetch_tag(tag_key)
        present = {value.casefold() for value in current.values}
        missing = [value for value in wanted if value.casefold() not in present]
        if missing:
            merged = [*current.values, *missing]
            try:
                self._factory.admin().tag_policies.update_tag_policy(
                    tag_key=current.tag_key,
                    tag_policy=TagPolicy(
                        tag_key=current.tag_key, values=[Value(name=value) for value in merged]
                    ),
                    update_mask="values",
                )
            except Exception as exc:  # noqa: BLE001
                raise UpstreamError(f"Could not update governed tag '{tag_key}': {exc}") from exc
            logger.info("Added values %s to governed tag %s", missing, tag_key)
        live = self._wait_until_values(tag_key, wanted)
        self._cache.invalidate(("governed-tag-policies",))
        return live

    def _fetch_tag(self, tag_key: str) -> GovernedTagSummary:
        """Read one tag policy from Unity Catalog, bypassing the list cache.

        Raises:
            InvalidRequestError: If no governed tag policy exists for the key.
            UpstreamError: If the tag policy cannot be read.
        """
        key = tag_key.strip()
        try:
            policy = self._factory.admin().tag_policies.get_tag_policy(tag_key=key)
        except NotFound as exc:
            raise InvalidRequestError(f"Governed tag '{key}' was not found") from exc
        except Exception as exc:  # noqa: BLE001
            raise UpstreamError(f"Could not read governed tag '{key}': {exc}") from exc
        return self._to_summary(policy)

    def _wait_until_values(self, tag_key: str, values: list[str]) -> GovernedTagSummary:
        """Poll the live tag policy until every requested value is present, or time out.

        Args:
            tag_key: Governed tag key.
            values: Values that must appear on the live policy.

        Returns:
            The latest live tag summary. Callers should still handle a lagging compiler.
        """
        needed = {value.casefold() for value in values if value}
        deadline = time.monotonic() + _TAG_VALUE_WAIT_SECONDS
        latest = self._fetch_tag(tag_key)
        while needed - {value.casefold() for value in latest.values}:
            if time.monotonic() >= deadline:
                missing = sorted(needed - {value.casefold() for value in latest.values})
                logger.warning(
                    "Governed tag %s is still missing values %s after waiting; continuing",
                    tag_key,
                    missing,
                )
                break
            time.sleep(_TAG_VALUE_POLL_SECONDS)
            latest = self._fetch_tag(tag_key)
        return latest

    def _all_tags(self) -> list[GovernedTagSummary]:
        """Return all governed tags from the cache, loading them on a miss."""
        return self._cache.get_or_load(("governed-tag-policies",), self._load_all)

    def _load_all(self) -> list[GovernedTagSummary]:
        """List every tag policy from the workspace, sorted by key.

        Raises:
            UpstreamError: If the tag policies cannot be listed.
        """
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
        """Convert an SDK tag policy into a summary, tolerating missing fields."""
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
