from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.errors import NotFound
from databricks.sdk.service.tags import TagPolicy, Value

from backend.app.clients.cache import TtlCache
from backend.app.core.errors import InvalidRequestError, UpstreamError
from backend.app.services import governed_tags
from backend.app.services.governed_tags import GovernedTagService


def _policy(key: str, values: list[str], description: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        tag_key=key,
        description=description,
        values=[SimpleNamespace(name=value) for value in values],
    )


class _FakeTagPolicies:
    """Tag policy API stand-in. Updates become visible after ``lag`` reads (default: at once)."""

    def __init__(
        self,
        policies: list[Any],
        *,
        lag: int = 0,
        list_error: Exception | None = None,
        get_error: Exception | None = None,
        update_error: Exception | None = None,
    ) -> None:
        self.policies = {p.tag_key: p for p in policies}
        self.lag = lag
        self.list_error = list_error
        self.get_error = get_error
        self.update_error = update_error
        self.updates: list[dict[str, Any]] = []
        self._pending: dict[str, list[Any]] = {}

    def list_tag_policies(self) -> list[Any]:
        if self.list_error is not None:
            raise self.list_error
        return list(self.policies.values())

    def get_tag_policy(self, tag_key: str) -> Any:
        if self.get_error is not None:
            raise self.get_error
        pending = self._pending.get(tag_key)
        if pending is not None:
            if pending[0] > 0:
                pending[0] -= 1
            else:
                self.policies[tag_key] = pending[1]
                del self._pending[tag_key]
        if tag_key not in self.policies:
            raise NotFound(f"no tag policy {tag_key}")
        return self.policies[tag_key]

    def update_tag_policy(self, *, tag_key: str, tag_policy: TagPolicy, update_mask: str) -> None:
        if self.update_error is not None:
            raise self.update_error
        self.updates.append({"tag_key": tag_key, "update_mask": update_mask, "tag_policy": tag_policy})
        updated = SimpleNamespace(
            tag_key=tag_key,
            description=getattr(self.policies.get(tag_key), "description", None),
            values=list(tag_policy.values),
        )
        if self.lag <= 0:
            self.policies[tag_key] = updated
        else:
            self._pending[tag_key] = [self.lag, updated]


class _RecordingCache(TtlCache):
    def __init__(self) -> None:
        super().__init__(ttl_seconds=60)
        self.invalidated: list[Any] = []

    def invalidate(self, key: Any = None) -> None:
        self.invalidated.append(key)
        super().invalidate(key)


def _service(tag_policies: _FakeTagPolicies, cache: TtlCache | None = None) -> GovernedTagService:
    factory = SimpleNamespace(admin=lambda: SimpleNamespace(tag_policies=tag_policies))
    return GovernedTagService(factory, cache or TtlCache(ttl_seconds=60))  # type: ignore[arg-type]


@pytest.fixture
def fast_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(governed_tags, "_TAG_VALUE_POLL_SECONDS", 0.001)
    monkeypatch.setattr(governed_tags, "_TAG_VALUE_WAIT_SECONDS", 0.05)


def _catalog() -> list[SimpleNamespace]:
    return [
        _policy("sensitivity_category", ["PII", "Confidential"], "How sensitive the data is"),
        _policy("mask_rule", ["hash", "redact"]),
        _policy("row_filter", ["region_us", "region_eu"], "Row-level scoping"),
    ]


def test_list_tags_sorts_and_filters_by_key_description_or_value() -> None:
    service = _service(_FakeTagPolicies(_catalog()))
    everything = service.list_tags()
    assert [tag.tag_key for tag in everything.tags] == ["mask_rule", "row_filter", "sensitivity_category"]
    assert everything.total == 3
    assert everything.truncated is False

    by_key = service.list_tags(q="MASK")
    assert [tag.tag_key for tag in by_key.tags] == ["mask_rule"]
    by_description = service.list_tags(q="scoping")
    assert [tag.tag_key for tag in by_description.tags] == ["row_filter"]
    by_value = service.list_tags(q="  confidential ")
    assert [tag.tag_key for tag in by_value.tags] == ["sensitivity_category"]
    assert service.list_tags(q="nothing-matches").total == 0


def test_list_tags_clamps_limit_and_flags_truncation() -> None:
    service = _service(_FakeTagPolicies(_catalog()))
    capped = service.list_tags(limit=2)
    assert len(capped.tags) == 2
    assert capped.total == 3
    assert capped.truncated is True
    floored = service.list_tags(limit=0)
    assert len(floored.tags) == 1
    assert service.list_tags(limit=10_000).truncated is False


def test_get_tag_prefers_cache_then_falls_back_to_direct_read() -> None:
    tag_policies = _FakeTagPolicies(_catalog())
    service = _service(tag_policies)
    assert service.get_tag(" mask_rule ").values == ["hash", "redact"]

    # Not in the list cache (added after the cache loaded): direct read still finds it.
    tag_policies.policies["late_tag"] = _policy("late_tag", ["x"])
    assert service.get_tag("late_tag").values == ["x"]


def test_get_tag_maps_missing_and_unreadable_tags() -> None:
    service = _service(_FakeTagPolicies(_catalog()))
    with pytest.raises(InvalidRequestError, match="was not found"):
        service.get_tag("does_not_exist")

    broken = _service(_FakeTagPolicies(_catalog(), get_error=RuntimeError("api down")))
    with pytest.raises(UpstreamError, match="Could not read governed tag"):
        broken.get_tag("does_not_exist")


def test_list_failure_surfaces_as_upstream_error() -> None:
    service = _service(_FakeTagPolicies([], list_error=RuntimeError("denied")))
    with pytest.raises(UpstreamError, match="Could not list governed tags"):
        service.list_tags()


def test_values_for_keys_unions_values_in_first_seen_order() -> None:
    service = _service(_FakeTagPolicies(_catalog()))
    assert service.values_for_keys(["sensitivity_category", "mask_rule"]) == [
        "PII",
        "Confidential",
        "hash",
        "redact",
    ]
    assert service.values_for_keys([]) == []
    with pytest.raises(InvalidRequestError):
        service.values_for_keys(["sensitivity_category", "missing"])


def test_require_tags_preserves_order_and_fails_on_missing() -> None:
    service = _service(_FakeTagPolicies(_catalog()))
    tags = service.require_tags(["row_filter", "mask_rule"])
    assert [tag.tag_key for tag in tags] == ["row_filter", "mask_rule"]
    with pytest.raises(InvalidRequestError):
        service.require_tags(["mask_rule", "missing"])


def test_ensure_values_is_a_no_op_when_values_already_exist() -> None:
    tag_policies = _FakeTagPolicies(_catalog())
    service = _service(tag_policies)
    tag = service.ensure_values("sensitivity_category", ["pii", ""])
    assert tag.values == ["PII", "Confidential"]
    assert tag_policies.updates == []


def test_ensure_values_appends_missing_values_and_invalidates_cache(
    fast_waits: None,
) -> None:
    del fast_waits
    tag_policies = _FakeTagPolicies(_catalog())
    cache = _RecordingCache()
    service = _service(tag_policies, cache)
    tag = service.ensure_values("mask_rule", ["hash", "mask", "encrypt"])

    assert tag.values == ["hash", "redact", "mask", "encrypt"]
    assert len(tag_policies.updates) == 1
    update = tag_policies.updates[0]
    assert update["update_mask"] == "values"
    assert [value.name for value in update["tag_policy"].values] == ["hash", "redact", "mask", "encrypt"]
    assert cache.invalidated == [("governed-tag-policies",)]


def test_ensure_values_waits_for_lagging_policy_to_show_new_values(fast_waits: None) -> None:
    del fast_waits
    tag_policies = _FakeTagPolicies(_catalog(), lag=2)
    service = _service(tag_policies)
    tag = service.ensure_values("row_filter", ["region_apac"])
    assert "region_apac" in tag.values


def test_ensure_values_returns_after_timeout_when_compiler_lags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(governed_tags, "_TAG_VALUE_WAIT_SECONDS", 0.0)
    monkeypatch.setattr(governed_tags, "_TAG_VALUE_POLL_SECONDS", 0.001)
    tag_policies = _FakeTagPolicies(_catalog(), lag=10_000)
    service = _service(tag_policies)
    tag = service.ensure_values("row_filter", ["region_apac"])
    assert "region_apac" not in tag.values


def test_ensure_values_reports_update_failures_and_missing_tags() -> None:
    service = _service(_FakeTagPolicies(_catalog(), update_error=RuntimeError("forbidden")))
    with pytest.raises(UpstreamError, match="Could not update governed tag 'mask_rule'"):
        service.ensure_values("mask_rule", ["brand_new"])
    with pytest.raises(InvalidRequestError):
        service.ensure_values("missing", ["x"])


def test_to_summary_tolerates_sparse_policy_objects() -> None:
    sparse = SimpleNamespace(tag_key="bare", description="", values=[SimpleNamespace(name=None), None])
    summary = GovernedTagService._to_summary(sparse)  # noqa: SLF001
    assert summary.tag_key == "bare"
    assert summary.description is None
    assert summary.values == []
    assert summary.value_count == 0

    empty = GovernedTagService._to_summary(object())  # noqa: SLF001
    assert empty.tag_key == ""
    assert empty.values == []


def test_real_sdk_tag_policy_values_are_read() -> None:
    policy = TagPolicy(tag_key="real", values=[Value(name="a"), Value(name="b")])
    summary = GovernedTagService._to_summary(policy)  # noqa: SLF001
    assert summary.values == ["a", "b"]
