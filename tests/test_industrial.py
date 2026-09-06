import json

import pytest


def test_production_auth_requires_valid_api_key(monkeypatch):
    from app.security import ApiKeyAuthenticator

    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("CRA_API_KEYS", json.dumps({"secret": {"subject": "alice", "tenantId": "acme", "role": "legal_reviewer"}}))

    auth = ApiKeyAuthenticator.from_environment()
    with pytest.raises(PermissionError, match="authentication"):
        auth.authenticate(None)
    assert auth.authenticate("secret").tenant_id == "acme"


def test_development_auth_bypasses_workspace_key_even_when_test_keys_exist(monkeypatch):
    from app.security import ApiKeyAuthenticator

    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("CRA_API_KEYS", json.dumps({"test": {"subject": "alice", "tenantId": "acme", "role": "admin"}}))

    principal = ApiKeyAuthenticator.from_environment().authenticate(None)
    assert principal.tenant_id == "local"


def test_audit_event_is_append_only_and_tenant_scoped(tmp_path):
    from app.audit import AuditStore

    audit = AuditStore(tmp_path / "audit.db")
    audit.record(tenant_id="acme", subject="alice", action="review.created", resource_id="run-1")
    audit.record(tenant_id="other", subject="bob", action="review.created", resource_id="run-2")

    events = audit.list_events("acme")
    assert len(events) == 1
    assert events[0]["subject"] == "alice"
    with pytest.raises(TypeError):
        events[0]["action"] = "mutated"


def test_trace_summary_aggregates_latency_error_and_token_cost(tmp_path):
    from app.observability import TraceStore

    traces = TraceStore(tmp_path / "trace.db")
    traces.record(tenant_id="acme", run_id="r1", stage="extract", elapsed_ms=100, input_tokens=20, output_tokens=10)
    traces.record(tenant_id="acme", run_id="r2", stage="extract", elapsed_ms=300, input_tokens=30, output_tokens=20, error="timeout")

    assert traces.summary("acme") == {"runs": 2, "errors": 1, "avgLatencyMs": 200, "inputTokens": 50, "outputTokens": 30}


def test_feedback_export_redacts_contract_text_and_keeps_decision(tmp_path):
    from app.feedback import FeedbackStore

    feedback = FeedbackStore(tmp_path / "feedback.db")
    feedback.record(tenant_id="acme", run_id="r1", finding_id="f1", decision="rejected", reason="业务不接受", contract_excerpt="甲方身份证号 123")

    exported = feedback.export_training_examples("acme")
    assert exported == [{"runId": "r1", "findingId": "f1", "decision": "rejected", "reason": "业务不接受"}]


def test_evaluation_gate_rejects_metrics_below_threshold():
    from app.evaluation_gate import EvaluationGate

    gate = EvaluationGate({"recall": 0.85, "falsePositiveRate": 0.15})
    assert gate.evaluate({"recall": 0.9, "falsePositiveRate": 0.1})["passed"] is True
    result = gate.evaluate({"recall": 0.8, "falsePositiveRate": 0.1})
    assert result["passed"] is False
    assert result["failures"] == ["recall"]


def test_retriever_filters_version_and_reranks_explainably():
    from app.legal.retrieval import LegalRetriever

    retriever = LegalRetriever([
        {"id": "A", "version": "2025", "source": "民法典", "article": "第1条", "text": "违约金应当合理"},
        {"id": "B", "version": "2024", "source": "民法典", "article": "第2条", "text": "违约金不得过高"},
    ])

    hits = retriever.search("违约金过高", version="2025")
    assert [hit["id"] for hit in hits] == ["A"]
    assert hits[0]["retrieval"]["strategy"] == "lexical_hybrid_rerank"


def test_platform_metrics_and_feedback_api_are_tenant_scoped(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app import api
    from app.feedback import FeedbackStore
    from app.observability import TraceStore
    from app.security import ApiKeyAuthenticator

    monkeypatch.setattr(api, "authenticator", ApiKeyAuthenticator(environment="production", keys={"key": {"subject": "alice", "tenantId": "acme", "role": "legal_reviewer"}}))
    monkeypatch.setattr(api, "trace_store", TraceStore(tmp_path / "trace.db"))
    monkeypatch.setattr(api, "feedback_store", FeedbackStore(tmp_path / "feedback.db"))
    api.trace_store.record(tenant_id="acme", run_id="r1", stage="report", elapsed_ms=42)

    client = TestClient(api.app)
    assert client.get("/platform/metrics", headers={"X-API-Key": "key"}).json()["runs"] == 1
    assert client.post("/review-runs/r1/findings/f1/feedback", headers={"X-API-Key": "key"}, json={"decision": "accepted", "reason": "准确"}).status_code == 200
    assert client.get("/platform/feedback/export", headers={"X-API-Key": "key"}).json()["examples"][0]["findingId"] == "f1"


def test_review_run_keeps_its_tenant(tmp_path):
    from app.review_runs import ReviewRunStore

    run = ReviewRunStore(tmp_path / "runs.db").create_run(contract_type="purchase", tenant_id="acme")
    assert run["tenantId"] == "acme"


def test_production_serves_frontend_and_api_under_api_prefix():
    from fastapi.testclient import TestClient
    from app.api import app

    client = TestClient(app)
    assert 'id="app"' in client.get("/").text
    assert client.get("/api/health").status_code == 200


def test_online_upload_refuses_to_silently_run_mock_when_no_model_is_configured(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app import api
    from app.review_runs import ReviewRunStore

    monkeypatch.setattr(api, "review_run_store", ReviewRunStore(tmp_path / "runs.db"))
    monkeypatch.setattr(api, "active_llm_config", lambda role: None)
    response = TestClient(api.app).post("/upload", data={"text": "测试合同", "contract_type": "purchase"})

    assert response.status_code == 503
    assert "审查模型" in response.json()["detail"]


def test_configured_non_deepseek_provider_is_not_marked_as_mock(monkeypatch):
    from app.config import using_mock
    from app import settings_store

    monkeypatch.delenv("DSH_FORCE_MOCK", raising=False)
    monkeypatch.setattr(settings_store, "active_llm_config", lambda role: {"provider": "opencode-go"})
    assert using_mock() is False


def test_review_history_lists_newest_runs_and_filters_by_status(tmp_path):
    from app.review_runs import ReviewRunStore

    store = ReviewRunStore(tmp_path / "runs.db")
    done = store.create_run(contract_type="purchase", tenant_id="acme")
    store.complete_run(done["id"], {"summary": {"total": 2}, "meta": {}}, elapsed_ms=1, stage_times=[1, 2, 3, 4])
    running = store.create_run(contract_type="sale", tenant_id="acme")

    assert [run["id"] for run in store.list_runs(tenant_id="acme")] == [running["id"], done["id"]]
    assert [run["id"] for run in store.list_runs(tenant_id="acme", status="done")] == [done["id"]]


def test_review_history_endpoint_is_tenant_scoped(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from app import api
    from app.review_runs import ReviewRunStore
    from app.security import ApiKeyAuthenticator

    store = ReviewRunStore(tmp_path / "runs.db")
    store.create_run(contract_type="purchase", tenant_id="acme")
    store.create_run(contract_type="sale", tenant_id="other")
    monkeypatch.setattr(api, "review_run_store", store)
    monkeypatch.setattr(api, "authenticator", ApiKeyAuthenticator(environment="production", keys={"key": {"subject": "alice", "tenantId": "acme", "role": "legal_reviewer"}}))

    response = TestClient(api.app).get("/review-runs", headers={"X-API-Key": "key"})
    assert response.status_code == 200
    assert len(response.json()["runs"]) == 1
