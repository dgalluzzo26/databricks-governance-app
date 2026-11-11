from __future__ import annotations

from fastapi.testclient import TestClient

from backend.tests.conftest import headers_for


def test_help_flows_returns_markdown(client: TestClient) -> None:
    response = client.get("/api/help/flows", headers=headers_for("user@example.com"))
    assert response.status_code == 200
    assert "text/plain" in response.headers.get("content-type", "")
    body = response.text
    assert body.startswith("# Developer guide")
    assert "Access Explorer" in body or "Access explorer" in body
    assert "row filter" in body.lower()


def test_help_docs_index_and_every_doc_loads(client: TestClient) -> None:
    user = headers_for("user@example.com")
    docs = client.get("/api/help/docs", headers=user).json()["docs"]
    slugs = [d["slug"] for d in docs]
    assert slugs == ["masking-row-filters", "grants-sync", "backend"]
    for slug in slugs:
        response = client.get(f"/api/help/docs/{slug}", headers=user)
        assert response.status_code == 200, slug
        assert response.text.startswith("# "), slug
        assert "```mermaid" in response.text, slug


def test_help_unknown_doc_is_404(client: TestClient) -> None:
    response = client.get("/api/help/docs/nope", headers=headers_for("user@example.com"))
    assert response.status_code == 404


def test_api_reference_lists_routes_with_access_levels(client: TestClient) -> None:
    body = client.get("/api/help/api-reference", headers=headers_for("user@example.com")).json()
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
    assert by_key[("GET", "/api/help/docs/{slug}")]["parameters"][0]["location"] == "path"
