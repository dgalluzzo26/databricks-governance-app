"""Nested account group resolution: parent membership inherits child groups."""

from __future__ import annotations

from databricks.sdk.errors import DatabricksError

from backend.app.clients.cache import TtlCache
from backend.app.models.user import CurrentUser
from backend.app.services.account_groups import (
    AccountGroupResolver,
    build_graph,
    groups_for_user,
)

USER_ID = "77269872132114"
OTHER_USER_ID = "5551234"


def _user(member_id: str) -> dict:
    return {"$ref": f"Users/{member_id}", "value": member_id, "display": "someone"}


def _group(group_id: str, name: str, members: list[dict]) -> dict:
    return {"id": group_id, "displayName": name, "members": members}


def _group_member(group_id: str, name: str) -> dict:
    return {"$ref": f"Groups/{group_id}", "value": group_id, "display": name}


def _fixture_resources() -> list[dict]:
    """dac_finance (direct member David) is nested in budget; contracts sits in pii_name."""
    return [
        _group("g-finance", "dac_finance", [_user(USER_ID), _group_member("g-other", "grp_other")]),
        _group("g-budget", "dac_finance_budget", [_group_member("g-finance", "dac_finance")]),
        _group("g-contracts", "dac_finance_contracts", [_group_member("g-finance", "dac_finance")]),
        _group(
            "g-pii",
            "dac_finance_contracts_pii_name",
            [_group_member("g-contracts", "dac_finance_contracts")],
        ),
        _group("g-unrelated", "dac_public", [_user(OTHER_USER_ID)]),
        _group("g-other", "grp_other", []),
    ]


def test_direct_member_inherits_every_child_group_transitively() -> None:
    graph = build_graph(_fixture_resources())
    assert groups_for_user(graph, USER_ID) == [
        "dac_finance",
        "dac_finance_budget",
        "dac_finance_contracts",
        "dac_finance_contracts_pii_name",
    ]


def test_membership_does_not_flow_down_to_parent_groups() -> None:
    # Being in a child group must not grant the parent: nesting points parent -> child.
    graph = build_graph(_fixture_resources())
    assert groups_for_user(graph, OTHER_USER_ID) == ["dac_public"]


def test_cycles_terminate() -> None:
    resources = [
        _group("a", "grp_a", [_user(USER_ID), _group_member("b", "grp_b")]),
        _group("b", "grp_b", [_group_member("a", "grp_a")]),
    ]
    assert groups_for_user(build_graph(resources), USER_ID) == ["grp_a", "grp_b"]


def test_unknown_user_has_no_account_groups() -> None:
    assert groups_for_user(build_graph(_fixture_resources()), "nobody") == []


class _FakeApiClient:
    def __init__(self, pages: list[dict]) -> None:
        self._pages = pages
        self.calls: list[dict] = []

    def do(self, method: str, path: str, query: dict) -> dict:
        self.calls.append(query)
        return self._pages[len(self.calls) - 1]


class _FakeAdmin:
    def __init__(self, api_client: _FakeApiClient) -> None:
        self.api_client = api_client


class _FakeFactory:
    def __init__(self, *, configured: bool = True, api_client: _FakeApiClient | None = None) -> None:
        self._configured = configured
        self._api_client = api_client
        self.admin_calls = 0

    @property
    def admin_configured(self) -> bool:
        return self._configured

    def admin(self) -> _FakeAdmin:
        self.admin_calls += 1
        assert self._api_client is not None
        return _FakeAdmin(self._api_client)


def test_resolver_reads_all_pages_and_caches_the_graph() -> None:
    resources = _fixture_resources()
    pages = [
        {"Resources": resources[:4], "totalResults": len(resources)},
        {"Resources": resources[4:], "totalResults": len(resources)},
    ]
    client = _FakeApiClient(pages)
    factory = _FakeFactory(api_client=client)
    resolver = AccountGroupResolver(factory, TtlCache(60))

    first = resolver.groups_for(USER_ID)
    second = resolver.groups_for(USER_ID)

    assert "dac_finance_budget" in first
    assert first == second
    assert [call["startIndex"] for call in client.calls] == [1, 5]
    assert factory.admin_calls == 1  # the graph is loaded once, then served from cache


def test_resolver_returns_nothing_when_admin_not_configured() -> None:
    factory = _FakeFactory(configured=False)
    assert AccountGroupResolver(factory, TtlCache(60)).groups_for(USER_ID) == []
    assert factory.admin_calls == 0


def test_resolver_degrades_to_no_extra_groups_on_scim_error() -> None:
    class _Failing(_FakeApiClient):
        def do(self, method: str, path: str, query: dict) -> dict:
            raise DatabricksError("scim unavailable")

    resolver = AccountGroupResolver(_FakeFactory(api_client=_Failing([])), TtlCache(60))
    assert resolver.groups_for(USER_ID) == []


def test_resolver_skips_lookup_without_user_id() -> None:
    factory = _FakeFactory(api_client=_FakeApiClient([]))
    assert AccountGroupResolver(factory, TtlCache(60)).groups_for(None) == []
    assert factory.admin_calls == 0


def test_attribute_groups_unions_workspace_and_nested_groups() -> None:
    user = CurrentUser(
        user_name="david.galluzzo@databricks.com",
        email="david.galluzzo@databricks.com",
        display_name="David Galluzzo",
        groups=["admins", "dac_finance"],
        effective_groups=["admins", "dac_finance", "dac_finance_budget"],
    )
    assert user.attribute_groups == ["admins", "dac_finance", "dac_finance_budget"]
    # Admin rights are not widened by nesting: is_admin only follows direct workspace groups.
    assert user.is_admin is False


def test_attribute_groups_falls_back_to_workspace_groups() -> None:
    user = CurrentUser(
        user_name="a@example.com",
        email="a@example.com",
        display_name="A",
        groups=["analysts"],
    )
    assert user.attribute_groups == ["analysts"]
