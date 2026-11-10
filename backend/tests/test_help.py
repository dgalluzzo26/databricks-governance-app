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
