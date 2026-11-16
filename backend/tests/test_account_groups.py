"""Account group resolution via Account SCIM Users/{id} (nested groups expanded)."""

from __future__ import annotations

from databricks.sdk.errors import DatabricksError

from backend.app.clients.cache import TtlCache
from backend.app.models.user import CurrentUser
from backend.app.services.account_groups import AccountGroupResolver, group_names_from_user_resource

USER_ID = "77269872132114"


def test_group_names_from_user_resource_dedupes_and_sorts() -> None:
    names = group_names_from_user_resource(
        {
            "groups": [
                {"display": "dac_finance_budget"},
                {"display": "dac_finance"},
                {"display": "dac_finance"},
                {"display": "  "},
                {},
            ]
        }
    )
    assert names == ["dac_finance", "dac_finance_budget"]


class _FakeApiClient:
    def __init__(self, responses: list[dict]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str, dict]] = []

    def do(self, method: str, path: str, query: dict) -> dict:
        self.calls.append((method, path, query))
        return self._responses[len(self.calls) - 1]


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


def test_resolver_loads_user_groups_once_then_caches() -> None:
    client = _FakeApiClient(
        [
            {
                "id": USER_ID,
                "groups": [
                    {"display": "dac_finance"},
                    {"display": "dac_finance_budget"},
                ],
            }
        ]
    )
    factory = _FakeFactory(api_client=client)
    resolver = AccountGroupResolver(factory, TtlCache(60))

    first = resolver.groups_for(USER_ID)
    second = resolver.groups_for(USER_ID)

    assert first == ["dac_finance", "dac_finance_budget"]
    assert first == second
    assert factory.admin_calls == 1
    assert len(client.calls) == 1
    method, path, query = client.calls[0]
    assert method == "GET"
    assert path.endswith(f"/Users/{USER_ID}")
    assert query == {"attributes": "id,groups"}


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
