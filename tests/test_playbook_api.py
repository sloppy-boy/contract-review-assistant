"""Public lifecycle and upload binding using temporary stores and no model calls."""
import copy

import pytest
from fastapi.testclient import TestClient

from app import api
from app.playbook_store import PlaybookStore
from app.review_runs import ReviewRunStore
from app.security import ApiKeyAuthenticator


@pytest.fixture
def service(monkeypatch, tmp_path):
    monkeypatch.setenv("DSH_FORCE_MOCK", "1")
    monkeypatch.setattr(api, "review_run_store", ReviewRunStore(tmp_path / "runs.db"))
    monkeypatch.setattr(api, "playbook_store", PlaybookStore(tmp_path / "books.db"), raising=False)
    monkeypatch.setattr(api, "authenticator", ApiKeyAuthenticator(environment="production", keys={
        "test-alice": {"subject": "alice", "tenantId": "acme", "role": "admin"},
        "test-bob": {"subject": "bob", "tenantId": "other", "role": "admin"},
        "test-reader": {"subject": "reader", "tenantId": "acme", "role": "reader"},
    }))
    monkeypatch.setattr(api, "active_llm_config", lambda role: {"provider": "test"})
    queued = []

    class QueuedThread:
        def __init__(self, *, target, args, daemon):
            self.target, self.args = target, args

        def start(self):
            queued.append((self.target, self.args))

    # Replace just the asynchronous launch boundary; storage and HTTP stay real.
    monkeypatch.setattr(api.threading, "Thread", QueuedThread)
    client = TestClient(api.app)
    client.headers["X-API-Key"] = "test-alice"
    return client, queued


def policy():
    return {
        "name": "采购测试标准", "contractType": "purchase", "jurisdiction": "CN",
        "businessScenario": "general", "effectiveScope": ["procurement"],
        "rules": [{
            "id": "p-1", "riskType": "付款条件不明确", "severity": "medium",
            "triggerCondition": "付款条件由一方确认", "reviewQuestion": "付款条件是否客观？",
            "acceptableCondition": "条件客观且期限明确", "suggestedClause": "双方约定明确的付款期限。",
            "escalationPolicy": "提交法务人工确认", "categoryId": "payment_invoice",
        }],
    }


def create_policy(client, *, publish=False):
    response = client.post("/playbooks", json=policy())
    assert response.status_code == 201
    book = response.json()
    if publish:
        response = client.post(f"/playbooks/{book['playbookId']}/versions/1/publish")
        assert response.status_code == 200
        book = response.json()
    return book


def test_public_lifecycle_preserves_published_version_and_actor_events(service):
    client, _ = service
    book = create_policy(client)
    base = f"/playbooks/{book['playbookId']}"
    updated = policy()
    updated["name"] = "更新后的测试标准"
    assert client.put(f"{base}/versions/1", json=updated).status_code == 200
    published = client.post(f"{base}/versions/1/publish").json()
    assert published["snapshot"]["content"]["name"] == "更新后的测试标准"
    assert published["createdBy"] == published["publishedBy"] == "alice"
    assert client.put(f"{base}/versions/1", json=policy()).status_code == 409
    assert client.post(f"{base}/versions/1/publish").status_code == 409
    next_version = client.post(f"{base}/versions", json={"sourceVersion": 1})
    assert next_version.status_code == 201
    assert next_version.json()["version"] == 2
    assert next_version.json()["status"] == "draft"
    archived = client.post(f"{base}/versions/1/archive").json()
    assert archived["snapshot"] == published["snapshot"]
    assert archived["archivedBy"] == "alice"
    assert client.get(f"{base}/versions/1").json()["status"] == "archived"
    assert [v["version"] for v in client.get(f"{base}/versions").json()["versions"]] == [2, 1]
    assert client.get("/api/playbooks").json()["playbooks"][0]["version"] == 2
    events = client.get(f"{base}/events").json()["events"]
    assert [e["type"] for e in events] == ["created", "updated", "published", "created", "archived"]
    assert all(e["subject"] == "alice" and e["createdAt"] for e in events)


def test_playbook_authentication_authorization_and_tenant_isolation(service):
    client, _ = service
    book = create_policy(client)
    base = f"/playbooks/{book['playbookId']}"
    client.headers.pop("X-API-Key")
    assert client.get("/playbooks").status_code == 401
    client.headers["X-API-Key"] = "test-reader"
    assert client.get(f"{base}/versions/1").status_code == 200
    assert client.post("/playbooks", json=policy()).status_code == 403
    assert client.put(f"{base}/versions/1", json=policy()).status_code == 403
    for suffix in ["publish", "archive"]:
        assert client.post(f"{base}/versions/1/{suffix}").status_code == 403
    assert client.post(f"{base}/versions", json={"sourceVersion": 1}).status_code == 403
    client.headers["X-API-Key"] = "test-bob"
    assert client.get("/playbooks").json()["playbooks"] == []
    for suffix in ["versions", "versions/1", "events"]:
        assert client.get(f"{base}/{suffix}").status_code == 404
    assert client.post(f"{base}/versions/1/publish").status_code == 404
    assert client.put(f"{base}/versions/1", json=policy()).status_code == 404
    assert client.post(f"{base}/versions", json={"sourceVersion": 1}).status_code == 404


def test_api_rejects_invalid_policy_and_version_data(service):
    client, _ = service
    malformed = policy()
    malformed["rules"][0]["severity"] = "critical"
    assert client.post("/playbooks", json=malformed).status_code == 422
    assert client.post("/playbooks", json={**policy(), "publishedBy": "forged"}).status_code == 422
    empty = {**policy(), "rules": []}
    book = client.post("/playbooks", json=empty).json()
    base = f"/playbooks/{book['playbookId']}"
    assert client.post(f"{base}/versions/1/publish").status_code == 409
    for version in [0, -1, True, 1.5]:
        assert client.post(f"{base}/versions", json={"sourceVersion": version}).status_code == 422


def test_queued_run_uses_bound_snapshot_after_new_publication_and_archive(service, monkeypatch):
    client, queued = service
    book = create_policy(client, publish=True)
    snapshot = copy.deepcopy(book["snapshot"])
    base = f"/playbooks/{book['playbookId']}"
    response = client.post("/upload", data={"text": "合成测试文本", "contract_type": "purchase", "playbook_id": book["playbookId"], "playbook_version": 1, "effective_scope": "procurement"})
    assert response.status_code == 200
    run_id = response.json()["taskId"]
    assert client.get(f"/report/{run_id}").json()["playbookSnapshot"] == snapshot
    assert client.post(f"{base}/versions", json={"sourceVersion": 1}).status_code == 201
    changed = policy()
    changed["rules"][0]["suggestedClause"] = "修订后的替换条款。"
    assert client.put(f"{base}/versions/2", json=changed).status_code == 200
    assert client.post(f"{base}/versions/2/publish").status_code == 200
    assert client.post(f"{base}/versions/1/archive").status_code == 200
    seen = []

    def fake_pipeline(content, *, contract_type, contract_name, progress, playbook_snapshot):
        seen.append(copy.deepcopy(playbook_snapshot))
        progress(0, "running")
        progress(0, "done")
        return {"risks": [], "meta": {"playbookSnapshot": {"version": 999}}}

    monkeypatch.setattr(api, "run_pipeline", fake_pipeline)
    target, args = queued.pop()
    target(*args)
    assert seen == [snapshot]
    finished = client.get(f"/report/{run_id}").json()
    assert finished["status"] == "done"
    assert finished["report"]["meta"]["playbookSnapshot"] == snapshot
    assert finished["report"]["meta"]["playbookApplication"] == "snapshot_only"
    api.review_run_store = ReviewRunStore(api.review_run_store.path)
    assert client.get(f"/report/{run_id}").json()["playbookSnapshot"] == snapshot
    assert client.get("/review-runs").json()["runs"][0]["playbookSnapshot"] == snapshot
    events = client.get(f"/review-runs/{run_id}/events").json()["events"]
    assert events[0]["data"]["playbook"]["version"] == 1
    client.headers["X-API-Key"] = "test-bob"
    assert client.get(f"/report/{run_id}").status_code == 404
    assert client.get(f"/review-runs/{run_id}/events").status_code == 404


def test_active_playbook_selector_keeps_active_version_when_latest_is_draft(service):
    client, _ = service
    book = create_policy(client, publish=True)
    base = f"/playbooks/{book['playbookId']}"
    assert client.post(f"{base}/versions", json={"sourceVersion": 1}).status_code == 201

    response = client.get("/api/playbooks", params={"activeOnly": "true"})
    assert response.status_code == 200
    assert [(item["playbookId"], item["version"], item["status"]) for item in response.json()["playbooks"]] == [(book["playbookId"], 1, "active")]


@pytest.mark.parametrize("case,expected", [
    ("draft", 409), ("archived", 409), ("missing", 404), ("tenant", 404),
    ("contract", 409), ("jurisdiction", 409), ("scenario", 409), ("scope", 409),
    ("only_id", 422), ("only_version", 422), ("version_zero", 422),
])
def test_invalid_upload_selection_never_enqueues(service, case, expected):
    client, queued = service
    book = create_policy(client, publish=case != "draft")
    data = {"text": "合成测试文本", "contract_type": "purchase", "playbook_id": book["playbookId"], "playbook_version": 1, "effective_scope": "procurement"}
    if case == "archived":
        client.post(f"/playbooks/{book['playbookId']}/versions/1/archive")
    if case == "missing":
        data["playbook_version"] = 999
    if case == "tenant":
        client.headers["X-API-Key"] = "test-bob"
    for selected_case, field, value in [("contract", "contract_type", "sale"), ("jurisdiction", "jurisdiction", "US"), ("scenario", "business_scenario", "special"), ("scope", "effective_scope", "outside"), ("version_zero", "playbook_version", 0)]:
        if case == selected_case:
            data[field] = value
    if case == "only_id":
        data.pop("playbook_version")
    if case == "only_version":
        data.pop("playbook_id")
    assert client.post("/upload", data=data).status_code == expected
    assert queued == []
    assert api.review_run_store.list_runs(tenant_id="acme") == []
    assert api.review_run_store.list_runs(tenant_id="other") == []


def test_legacy_upload_binds_builtin_and_legacy_records_stay_unbound(service):
    client, queued = service
    legacy = api.review_run_store.create_run(contract_type="purchase", tenant_id="acme")
    assert client.get(f"/report/{legacy['id']}").json()["playbookSnapshot"] is None
    response = client.post("/upload", data={"text": "合成测试文本", "contract_type": "purchase"})
    assert response.status_code == 200
    run = client.get(f"/report/{response.json()['taskId']}").json()
    assert run["playbookSnapshot"]["tenantId"] == "acme"
    assert run["playbookSnapshot"]["playbookId"].startswith("system-risk-matrix-baseline")
    assert run["playbookSnapshot"]["content"]["rules"]
    assert len(queued) == 1


def test_nondefault_scope_requires_explicit_selection(service):
    client, queued = service
    assert client.post("/upload", data={"text": "合成测试文本", "jurisdiction": "US"}).status_code == 422
    assert queued == []


@pytest.mark.parametrize("explicit", [False, True])
def test_upload_normalizes_scope_consistently_for_binding_and_execution(service, explicit):
    client, queued = service
    data = {"text": "合成测试文本", "contract_type": " purchase ", "jurisdiction": " CN ", "business_scenario": " general ", "effective_scope": " procurement "}
    if explicit:
        book = create_policy(client, publish=True)
        data.update(playbook_id=f" {book['playbookId']} ", playbook_version=1)
    response = client.post("/upload", data=data)
    assert response.status_code == 200
    run = client.get(f"/report/{response.json()['taskId']}").json()
    assert run["contract_type"] == "purchase"
    assert run["playbookSnapshot"]["content"]["contractType"] == "purchase"
    assert queued[0][1][2] == "purchase"


def test_whitespace_contract_type_does_not_produce_server_error(service):
    client = TestClient(api.app, raise_server_exceptions=False, headers={"X-API-Key": "test-alice"})
    response = client.post("/upload", data={"text": "合成测试文本", "contract_type": " purchase "})
    assert response.status_code == 200


@pytest.mark.parametrize("field", ["contract_type", "jurisdiction", "business_scenario", "effective_scope"])
def test_upload_rejects_blank_scope_without_creating_a_run(service, field):
    client, queued = service
    assert client.post("/upload", data={"text": "合成测试文本", field: "  "}).status_code == 422
    assert queued == []
    assert api.review_run_store.list_runs(tenant_id="acme") == []
