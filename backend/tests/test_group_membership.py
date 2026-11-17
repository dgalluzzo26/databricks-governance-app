"""Tests for Account SCIM group expansion used by the usage rollup."""

from __future__ import annotations

from typing import Any

from backend.app.services import group_membership as gm


class _FakeScim:
    """Serves paged SCIM Groups/Users listings from in-memory resources."""

    def __init__(self, groups: list[dict[str, Any]], users: list[dict[str, Any]], page: int = 2) -> None:
        self._groups = groups
        self._users = users
        self._page = page
        self.calls: list[str] = []

    def do(self, method: str, path: str, query: dict[str, Any] | None = None) -> Any:
        assert method == "GET"
        self.calls.append(path)
        resources = self._groups if path.endswith("/Groups") else self._users
        start = int((query or {}).get("startIndex", 1))
        count = min(int((query or {}).get("count", self._page)), self._page)
        page = resources[start - 1 : start - 1 + count]
        return {"Resources": page, "totalResults": len(resources), "startIndex": start}


class _Client:
    def __init__(self, scim: _FakeScim) -> None:
        self.api_client = scim


def _user(uid: str, email: str) -> dict[str, Any]:
    return {"id": uid, "userName": email}


def test_expands_direct_users_and_pages_through_listings() -> None:
    users = [_user("u1", "ana@example.com"), _user("u2", "bo@example.com"), _user("u3", "cy@example.com")]
    groups = [
        {
            "id": "g1",
            "displayName": "finance",
            "members": [
                {"value": "u1", "display": "Ana Smith", "$ref": "Users/u1"},
                {"value": "u3", "display": "Cy Jones", "$ref": "Users/u3"},
            ],
        },
        {
            "id": "g2",
            "displayName": "other",
            "members": [{"value": "u2", "display": "Bo", "$ref": "Users/u2"}],
        },
    ]
    scim = _FakeScim(groups, users, page=1)
    result = gm.expand_group_members(_Client(scim), ["Finance"])
    assert result == {"finance": {"ana@example.com", "cy@example.com"}}
    assert scim.calls.count("/api/2.0/account/scim/v2/Groups") >= 2


def test_follows_nested_groups_and_stops_on_cycles() -> None:
    users = [_user("u1", "ana@example.com"), _user("u2", "bo@example.com")]
    groups = [
        {
            "id": "outer",
            "displayName": "outer",
            "members": [
                {"value": "inner", "display": "inner", "$ref": "Groups/inner"},
            ],
        },
        {
            "id": "inner",
            "displayName": "inner",
            "members": [
                {"value": "u1", "display": "Ana", "$ref": "Users/u1"},
                {"value": "outer", "display": "outer", "$ref": "Groups/outer"},
            ],
        },
        {
            "id": "deep",
            "displayName": "deep",
            "members": [
                {"value": "u2", "display": "Bo", "$ref": "Users/u2"},
            ],
        },
    ]
    result = gm.expand_group_members(_Client(_FakeScim(groups, users)), ["outer"])
    assert result == {"outer": {"ana@example.com"}}


def test_resolves_display_name_members_via_scim_id_and_skips_service_principals() -> None:
    users = [_user("u1", "ana@example.com")]
    groups = [
        {
            "id": "g1",
            "displayName": "ops",
            "members": [
                {"value": "u1", "display": "Ana Smith", "$ref": "Users/u1"},
                {"value": "sp1", "display": "sp-automation", "$ref": "ServicePrincipals/sp1"},
                {"value": "x9", "display": "unknown@example.com", "$ref": "Users/x9"},
            ],
        },
    ]
    result = gm.expand_group_members(_Client(_FakeScim(groups, users)), ["ops"])
    assert result == {"ops": {"ana@example.com", "unknown@example.com"}}


def test_missing_group_maps_to_empty_set() -> None:
    result = gm.expand_group_members(_Client(_FakeScim([], [])), ["ghost"])
    assert result == {"ghost": set()}


def test_no_names_makes_no_calls() -> None:
    scim = _FakeScim([], [])
    assert gm.expand_group_members(_Client(scim), ["", "  "]) == {}
    assert scim.calls == []


def test_pages_when_total_results_is_a_string() -> None:
    users = [_user("u1", "ana@example.com"), _user("u2", "bo@example.com")]
    groups = [
        {"id": "g1", "displayName": "finance", "members": [
            {"value": "u1", "display": "Ana", "$ref": "Users/u1"},
        ]},
        {"id": "g2", "displayName": "ops", "members": [
            {"value": "u2", "display": "Bo", "$ref": "Users/u2"},
        ]},
    ]

    class _StringTotal(_FakeScim):
        def do(self, method: str, path: str, query: dict[str, Any] | None = None) -> Any:
            payload = super().do(method, path, query)
            payload["totalResults"] = str(payload["totalResults"])
            return payload

    result = gm.expand_group_members(_Client(_StringTotal(groups, users, page=1)), ["ops"])
    assert result == {"ops": {"bo@example.com"}}
