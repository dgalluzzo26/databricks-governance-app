from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.app.core.config import AppConfig
from backend.app.core.container import ServiceContainer
from backend.app.core.errors import UnauthorizedError
from backend.app.services.request_context import RequestContextResolver
from backend.tests.conftest import headers_for

ADMIN = headers_for("admin@example.com")
ANALYST = headers_for("analyst@example.com")


def test_help_flows_is_admin_only(client: TestClient) -> None:
    response = client.get("/api/help/flows", headers=ADMIN)
    assert response.status_code == 200
    assert "text/plain" in response.headers.get("content-type", "")
    body = response.text
    assert body.startswith("# Developer guide")
    assert "Access Explorer" in body or "Access explorer" in body
    assert "row filter" in body.lower()
    assert client.get("/api/help/flows", headers=ANALYST).status_code == 403


def test_admin_sees_and_loads_every_doc(client: TestClient) -> None:
    docs = client.get("/api/help/docs", headers=ADMIN).json()["docs"]
    by_slug = {d["slug"]: d for d in docs}
    assert list(by_slug) == ["user", "approver", "masking-row-filters", "grants-sync", "backend"]
    assert by_slug["user"]["audiences"] == ["user"]
    assert by_slug["approver"]["audiences"] == ["approver"]
    assert by_slug["grants-sync"]["audiences"] == ["admin"]
    for slug, meta in by_slug.items():
        response = client.get(f"/api/help/docs/{slug}", headers=ADMIN)
        assert response.status_code == 200, slug
        assert response.text.startswith("# "), slug
        if meta["audiences"] == ["admin"]:
            assert "```mermaid" in response.text, slug


def test_user_reads_only_the_user_guide(client: TestClient) -> None:
    docs = client.get("/api/help/docs", headers=ANALYST).json()["docs"]
    assert [d["slug"] for d in docs] == ["user"]
    assert client.get("/api/help/docs/user", headers=ANALYST).status_code == 200
    for slug in ("approver", "masking-row-filters", "grants-sync", "backend"):
        assert client.get(f"/api/help/docs/{slug}", headers=ANALYST).status_code == 404, slug


def test_approver_reads_user_and_approver_guides(
    client: TestClient, container: ServiceContainer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(container.access_requests, "is_category_approver", lambda _user: True)
    docs = client.get("/api/help/docs", headers=ANALYST).json()["docs"]
    assert [d["slug"] for d in docs] == ["user", "approver"]
    assert client.get("/api/help/docs/approver", headers=ANALYST).status_code == 200
    assert client.get("/api/help/docs/grants-sync", headers=ANALYST).status_code == 404


def test_help_unknown_doc_is_404(client: TestClient) -> None:
    response = client.get("/api/help/docs/nope", headers=ADMIN)
    assert response.status_code == 404


def test_builtin_openapi_pages_are_disabled(client: TestClient) -> None:
    for path in ("/docs", "/redoc", "/openapi.json"):
        response = client.get(path, headers=ADMIN)
        assert "swagger" not in response.text.lower(), path
        assert '"openapi"' not in response.text, path


def test_local_identity_requires_opt_in(config: AppConfig, container: ServiceContainer) -> None:
    factory = container.request_context._factory  # noqa: SLF001
    cache = container.request_context._cache  # noqa: SLF001
    resolver = RequestContextResolver(config, factory, cache)
    with pytest.raises(UnauthorizedError):
        resolver.resolve({})
    local = config.model_copy(update={"allow_local_identity": True, "dev_user_email": "dev@example.com"})
    resolver = RequestContextResolver(local, factory, cache)
    assert resolver.resolve({}).user_name == "dev@example.com"


def test_api_reference_is_admin_only_and_lists_access_levels(client: TestClient) -> None:
    assert client.get("/api/help/api-reference", headers=ANALYST).status_code == 403
    body = client.get("/api/help/api-reference", headers=ADMIN).json()
    by_key = {(e["method"], e["path"]): e for e in body["endpoints"]}

    run = by_key[("POST", "/api/data-access/runs")]
    assert run["access"] == "admin"
    assert run["tag"] == "data-access"
    assert run["requestBody"] == "RunDataAccessRequest | None"
    assert run["responseModel"] == "DataAccessRun"
    assert run["handler"] == "backend.app.api.routes.data_access.run_now"
    assert run["summary"].startswith("Run a data-access sync now")

    runs = by_key[("GET", "/api/data-access/runs")]
    params = {p["name"]: p for p in runs["parameters"]}
    assert params["mode"]["location"] == "query"
    assert params["page_size"]["required"] is False

    assert by_key[("GET", "/api/me")]["access"] == "user"
    assert by_key[("GET", "/api/health")]["access"] == "public"
    assert by_key[("GET", "/api/help/docs")]["access"] == "user"
    assert by_key[("GET", "/api/help/api-reference")]["access"] == "admin"
    assert by_key[("GET", "/api/help/docs/{slug}")]["parameters"][0]["location"] == "path"
