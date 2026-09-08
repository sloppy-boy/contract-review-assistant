"""Evidence cards and authoritative Word review packages; synthetic content only."""
import io

import pytest
from docx import Document
from fastapi.testclient import TestClient

from app import api
from app.nodes.report import report_node
from app.review_runs import ReviewRunStore
from app.security import ApiKeyAuthenticator
from app.state import ClauseFact, Finding


def synthetic_report():
    finding = Finding(id="risk-1", worker="payment", clauseId="3", clauseQuote="款项应于次日支付",
                      riskType="付款期限", severity="high", evidence="第3条规定次日支付",
                      suggestion="延长付款期限", suggestionClauseText="验收后30日内支付", status="upheld")
    return report_node({"findings": [finding], "clauses": [ClauseFact(clauseId="3", quote=finding.clauseQuote, location="第2页第4段")],
                        "contract_name": "合成审查包", "contract_type": "purchase"}, mode="B")["report"]


def doc_text(content):
    return "\n".join(p.text for p in Document(io.BytesIO(content)).paragraphs)


def test_evidence_card_has_stable_location_unknown_confidence_and_pending():
    report = synthetic_report()
    risk = report["risks"][0]
    assert risk["location"] == "第2页第4段"
    assert risk["paragraphId"] == report["clauses"][0]["paragraphId"] == "P0001"
    assert risk["source"] == "model"
    assert risk["confidence"] is None
    assert risk["requiresHumanReview"] is True
    assert risk["humanStatus"] == "pending"


@pytest.fixture
def service(monkeypatch, tmp_path):
    store = ReviewRunStore(tmp_path / "runs.db")
    monkeypatch.setattr(api, "review_run_store", store)
    monkeypatch.setattr(api, "authenticator", ApiKeyAuthenticator(environment="production", keys={
        "reviewer": {"subject": "reviewer-1", "tenantId": "acme", "role": "legal_reviewer"},
        "outsider": {"subject": "outsider", "tenantId": "other", "role": "admin"},
        "reader": {"subject": "reader", "tenantId": "acme", "role": "reader"},
    }))
    run = store.create_run(contract_type="purchase", tenant_id="acme")
    store.complete_run(run["id"], synthetic_report(), elapsed_ms=1, stage_times=[1, 0, 0, 0])
    return TestClient(api.app, headers={"X-API-Key": "reviewer"}), store, run["id"]


def test_every_human_decision_requires_reason_and_records_actor(service):
    client, store, run_id = service
    endpoint = f"/review-runs/{run_id}/findings/risk-1/disposition"
    assert client.post(endpoint, json={"decision": "accepted", "reason": " "}).status_code == 422
    for decision in ("accepted", "accepted_with_changes", "rejected", "escalated", "pending"):
        response = client.post(endpoint, json={"decision": decision, "reason": f"合成理由 {decision}"})
        assert response.status_code == 200, response.text
        assert response.json()["actor"] == "reviewer-1"
    assert len([e for e in store.list_events(run_id) if e["type"] == "disposition"]) == 5
    assert client.get(f"/report/{run_id}").json()["report"]["risks"][0]["humanStatus"] == "pending"


def test_disposition_and_export_respect_tenant_role_and_finding(service):
    client, _, run_id = service
    endpoint = f"/review-runs/{run_id}/findings/risk-1/disposition"
    assert client.post(endpoint, headers={"X-API-Key": "outsider"}, json={"decision": "accepted", "reason": "x"}).status_code == 404
    assert client.post(endpoint, headers={"X-API-Key": "reader"}, json={"decision": "accepted", "reason": "x"}).status_code == 403
    assert client.post(endpoint.replace("risk-1", "missing"), json={"decision": "accepted", "reason": "x"}).status_code == 404
    assert client.post("/export/word", headers={"X-API-Key": "outsider"}, json={"runId": run_id}).status_code == 404


def test_bound_docx_uses_server_report_dispositions_and_timeline(service):
    client, _, run_id = service
    client.post(f"/review-runs/{run_id}/findings/risk-1/disposition", json={"decision": "escalated", "reason": "请法务会审"})
    response = client.post("/export/word", json={"runId": run_id, "report": {"contract": {"name": "伪造合同"}, "reviewTimeline": [{"type": "伪造审计"}]}})
    assert response.status_code == 200
    text = doc_text(response.content)
    for expected in ("合成审查包", "P0001", "第2页第4段", "第3条规定次日支付", "验收后30日内支付", "置信度：未提供", "请法务会审", "reviewer-1", "审查时间线", run_id):
        assert expected in text
    assert "伪造合同" not in text and "伪造审计" not in text


def test_unbound_legacy_docx_is_explicit_and_does_not_trust_audit(service):
    client, _, _ = service
    report = synthetic_report()
    report["risks"][0]["reviewDecision"] = {"decision": "accepted", "reason": "伪造裁决"}
    report["reviewTimeline"] = [{"type": "伪造事件"}]
    report["meta"]["apiKey"] = "secret-key-marker"
    report["meta"]["prompt"] = "internal-prompt-marker"
    response = client.post("/export/word", json={"report": report})
    assert response.status_code == 200
    text = doc_text(response.content)
    assert "未绑定运行" in text
    for secret in ("伪造裁决", "伪造事件", "secret-key-marker", "internal-prompt-marker"):
        assert secret not in text
