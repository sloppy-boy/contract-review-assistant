from fastapi.testclient import TestClient

from app import api
from app.review_runs import ReviewRunStore
from app.security import ApiKeyAuthenticator
from app.visitor_sessions import VisitorSessionManager


def _client(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "authenticator", ApiKeyAuthenticator(environment="production", keys={
        "admin-key": {"subject": "admin", "tenantId": "acme", "role": "admin"},
    }))
    monkeypatch.setattr(api, "visitor_sessions", VisitorSessionManager("test-visitor-secret", 3600))
    monkeypatch.setattr(api, "review_run_store", ReviewRunStore(tmp_path / "runs.db"))
    return TestClient(api.app)


def _visitor(client):
    response = client.post("/visitor/session")
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "visitor"
    return {"Authorization": f"Bearer {body['token']}"}


def test_new_visitor_can_read_only_its_empty_history(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    assert client.get("/review-runs").status_code == 401
    headers = _visitor(client)
    response = client.get("/review-runs", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"runs": []}


def test_visitor_cannot_read_another_visitors_run(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)
    first = _visitor(client)
    second = _visitor(client)
    first_principal = api.visitor_sessions.authenticate(first["Authorization"].removeprefix("Bearer "))
    run = api.review_run_store.create_run(contract_type="purchase", tenant_id=first_principal.tenant_id)

    assert client.get(f"/report/{run['id']}", headers=first).status_code == 200
    assert client.get(f"/report/{run['id']}", headers=second).status_code == 404


def test_visitor_cannot_access_settings_but_admin_can(monkeypatch, tmp_path):
    client = _client(monkeypatch, tmp_path)

    assert client.get("/settings", headers=_visitor(client)).status_code == 403
    assert client.get("/settings", headers={"X-API-Key": "admin-key"}).status_code == 200
