from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from backend.app.models.identity_attribute import (
    AssignAttributeRequest,
    UpsertAttributeDefinitionRequest,
)
from backend.app.models.principal import PrincipalKind
from backend.app.services.identity_attributes import IdentityAttributeService
from backend.tests.conftest import headers_for
from backend.tests.test_identity_attributes import _admin, _service


def _seed(service: IdentityAttributeService) -> None:
    actor = _admin()
    service.upsert_definition(
        UpsertAttributeDefinitionRequest(key="domain", governed_tag_keys=["dga_domain"]),
        actor,  # type: ignore[arg-type]
    )
    for principal in ("analyst@example.com", "jane.analyst@example.com", "budget_owner@example.com"):
        service.assign(
            AssignAttributeRequest(
                principal_name=principal,
                principal_type=PrincipalKind.USER,
                attribute_key="domain",
                attribute_value="finance",
                expires_at=datetime.now(UTC) + timedelta(days=7),
            ),
            actor,  # type: ignore[arg-type]
        )


def test_principal_search_matches_partial_names_case_insensitively(container) -> None:  # noqa: ANN001
    service = _service(container)
    _seed(service)

    partial = service.list_assignments(principal_search="ANALY")
    assert sorted(a.principal_name for a in partial.assignments) == [
        "analyst@example.com",
        "jane.analyst@example.com",
    ]

    # Exact match is unchanged: a partial name does not match.
    assert service.list_assignments(principal_name="analy").total == 0
    assert service.list_assignments(principal_name="analyst@example.com").total == 1


def test_principal_search_treats_wildcards_literally(container) -> None:  # noqa: ANN001
    service = _service(container)
    _seed(service)

    # "%" and "_" are plain characters in a name search, not SQL wildcards.
    assert service.list_assignments(principal_search="%").total == 0
    assert service.list_assignments(principal_search="budget_owner").total == 1
    assert service.list_assignments(principal_search="budget?owner").total == 0


def test_blank_principal_search_does_not_filter(container) -> None:  # noqa: ANN001
    service = _service(container)
    _seed(service)

    assert service.list_assignments(principal_search="   ").total == 3


def test_api_principal_search_query_parameter(client: TestClient, container) -> None:  # noqa: ANN001
    service = _service(container)
    _seed(service)
    admin = headers_for("admin@example.com")

    response = client.get("/api/identity/assignments?principal_search=jane", headers=admin)
    assert response.status_code == 200
    names = [item["principalName"] for item in response.json()["assignments"]]
    assert names == ["jane.analyst@example.com"]
