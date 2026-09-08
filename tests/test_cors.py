from fastapi.testclient import TestClient


def test_configured_frontend_origin_is_allowed(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://frontend.example.test")
    from app import api

    response = TestClient(api.app).get("/health", headers={"Origin": "https://frontend.example.test"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "https://frontend.example.test"
