from __future__ import annotations

from fastapi.testclient import TestClient

from backend.tests.conftest import HOST, headers_for


def test_health(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_me_resolves_forwarded_user_and_admin_group(client: TestClient) -> None:
    body = client.get("/api/me", headers=headers_for("admin@example.com")).json()
    assert body["userName"] == "admin@example.com"
    assert body["displayName"] == "Test User"
    assert body["groups"] == ["admins", "users"]
    assert body["isAdmin"] is True
    assert body["workspaceHost"] == HOST


def test_me_prefers_user_token_for_groups(client: TestClient) -> None:
    headers = {**headers_for("analyst@example.com"), "X-Forwarded-Access-Token": "user-token"}
    body = client.get("/api/me", headers=headers).json()
    assert body["groups"] == ["admins", "data-stewards"]
    assert body["displayName"] == "Token User"
    assert body["isAdmin"] is True


def test_me_non_admin(client: TestClient) -> None:
    body = client.get("/api/me", headers=headers_for("analyst@example.com")).json()
    assert body["isAdmin"] is False


def test_me_unknown_user_falls_back_to_header_identity(client: TestClient) -> None:
    body = client.get("/api/me", headers=headers_for("new@example.com", "New Person")).json()
    assert body["userName"] == "new@example.com"
    assert body["displayName"] == "New Person"
    assert body["groups"] == []


def test_me_without_headers_uses_local_identity(client: TestClient) -> None:
    body = client.get("/api/me").json()
    assert body["userName"] == "local@example.com"


def test_app_info_reports_storage_and_admin_sp(client: TestClient) -> None:
    body = client.get("/api/app-info", headers=headers_for("analyst@example.com")).json()
    assert body["storage"] == {
        "backend": "memory",
        "healthy": True,
        "detail": "In-memory storage; data is not persisted",
    }
    assert body["authMode"] == "service_principal"
    assert body["adminSecretScope"] == "databricks-governance-app"
    assert body["adminSpConfigured"] is True


def test_unknown_api_route_is_json_404(client: TestClient) -> None:
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
