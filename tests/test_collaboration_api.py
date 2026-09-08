"""Collaboration HTTP workflow with temporary databases and synthetic reports."""
import pytest
from fastapi.testclient import TestClient

from app import api
from app.review_runs import ReviewRunStore
from app.security import ApiKeyAuthenticator


@pytest.fixture
def service(monkeypatch, tmp_path):
    from app.collaboration import CollaborationStore

    monkeypatch.setattr(api, "collaboration_store", CollaborationStore(tmp_path / "collaboration.db"), raising=False)
    monkeypatch.setattr(api, "review_run_store", ReviewRunStore(tmp_path / "runs.db"))
    monkeypatch.setattr(api, "authenticator", ApiKeyAuthenticator(environment="production", keys={
        "test-requester": {"subject": "requester", "tenantId": "acme", "role": "requester"},
        "test-reviewer": {"subject": "reviewer", "tenantId": "acme", "role": "legal_reviewer"},
        "test-approver": {"subject": "approver", "tenantId": "acme", "role": "legal_reviewer"},
        "test-admin": {"subject": "admin", "tenantId": "acme", "role": "admin"},
        "test-reader": {"subject": "reader", "tenantId": "acme", "role": "reader"},
        "test-other": {"subject": "outsider", "tenantId": "other", "role": "admin"},
    }))
    client = TestClient(api.app)
    client.headers["X-API-Key"] = "test-requester"
    return client


def create(client):
    response = client.post("/collaboration/requests", json={"title": "合成协作事项", "contractType": "purchase", "description": "测试发起单"})
    assert response.status_code == 201
    return response.json()


def action(client, request, suffix, **data):
    response = client.post(f"/collaboration/requests/{request['id']}/{suffix}", json={"expectedRevision": request["revision"], **data})
    assert response.status_code == 200, response.text
    return response.json()


def assigned(client):
    request = action(client, create(client), "submit")
    return action(client, request, "assign", assignee="reviewer", approver="approver", dueAt="2099-01-01T00:00:00Z")


def complete_run(*, tenant="acme", contract_type="purchase"):
    run = api.review_run_store.create_run(contract_type=contract_type, tenant_id=tenant)
    return api.review_run_store.complete_run(run["id"], {"risks": [{"id": "synthetic-risk"}], "meta": {"mock": True}}, elapsed_ms=0, stage_times=[0]*4)


def test_review_collaboration_end_to_end_with_approval_snapshot_and_notifications(service):
    client = service
    request = assigned(client)
    assert request["status"] == "in_review"
    client.headers["X-API-Key"] = "test-reviewer"
    run = complete_run()
    api.review_run_store.set_disposition(run["id"], "synthetic-risk", "accepted", "合成处置")
    request = action(client, request, "run", runId=run["id"])
    request = action(client, request, "comments", body="请确认此审查依据", mentions=["approver"])
    request = action(client, request, "request-approval")
    assert request["status"] == "pending_approval"
    assert request["approvalSnapshot"]["runId"] == run["id"]
    assert request["approvalSnapshot"]["dispositions"]["synthetic-risk"]["decision"] == "accepted"
    client.headers["X-API-Key"] = "test-approver"
    inbox = client.get("/collaboration/notifications").json()["notifications"]
    assert any(n["requestId"] == request["id"] for n in inbox)
    notification = inbox[0]
    first_read = client.post(f"/collaboration/notifications/{notification['id']}/read").json()
    second_read = client.post(f"/collaboration/notifications/{notification['id']}/read").json()
    assert first_read["readAt"] == second_read["readAt"]
    request = action(client, request, "decide", decision="approved", reason="同意此合成审查结论")
    assert request["status"] == "approved"
    original_snapshot = request["approvalSnapshot"]
    api.review_run_store.complete_run(run["id"], {"meta": {"changed": True}}, elapsed_ms=0, stage_times=[0]*4)
    detail = client.get(f"/collaboration/requests/{request['id']}").json()
    assert detail["approvalSnapshot"] == original_snapshot
    assert detail["approvalRound"] == 1
    assert detail["isOverdue"] is False
    events = client.get(f"/collaboration/requests/{request['id']}/events").json()["events"]
    assert any(e["data"].get("body") == "请确认此审查依据" for e in events)
    assert any(e["data"].get("reason") == "同意此合成审查结论" for e in events)
    assert client.get("/api/collaboration/requests?status=approved").json()["requests"][0]["id"] == request["id"]


def test_members_and_reads_are_authenticated_tenant_scoped_and_secret_free(service):
    client = service
    request = create(client)
    assert client.get("/collaboration/me").json() == {"subject": "requester", "tenantId": "acme", "role": "requester"}
    members = client.get("/collaboration/members").json()["members"]
    assert {m["subject"] for m in members} == {"requester", "reviewer", "approver", "admin", "reader"}
    assert all(set(m) == {"subject", "role"} for m in members)
    client.headers.pop("X-API-Key")
    assert client.get("/collaboration/requests").status_code == 401
    client.headers["X-API-Key"] = "test-other"
    assert client.get("/collaboration/requests").json()["requests"] == []
    for suffix in ["", "/events"]:
        assert client.get(f"/collaboration/requests/{request['id']}{suffix}").status_code == 404
    assert client.post(f"/collaboration/requests/{request['id']}/submit", json={"expectedRevision": 1}).status_code == 404


@pytest.mark.parametrize("case", ["outsider", "reader", "missing", "naive_date", "past_date"])
def test_invalid_assignment_never_changes_request_or_timeline(service, case):
    client = service
    request = action(client, create(client), "submit")
    base = f"/collaboration/requests/{request['id']}"
    events = client.get(base + "/events").json()
    payload = {"expectedRevision": request["revision"], "assignee": "reviewer", "approver": "approver", "dueAt": "2099-01-01T00:00:00Z"}
    if case in {"outsider", "reader", "missing"}:
        payload["assignee"] = case
    else:
        payload["dueAt"] = "2099-01-01T00:00:00" if case == "naive_date" else "2000-01-01T00:00:00Z"
    assert client.post(base + "/assign", json=payload).status_code in {409, 422}
    assert client.get(base).json()["revision"] == request["revision"]
    assert client.get(base + "/events").json() == events


def test_binding_requires_same_tenant_type_and_completed_report(service):
    client = service
    request = assigned(client)
    client.headers["X-API-Key"] = "test-reviewer"
    foreign = complete_run(tenant="other")
    wrong_type = complete_run(contract_type="sale")
    queued = api.review_run_store.create_run(contract_type="purchase", tenant_id="acme")
    base = f"/collaboration/requests/{request['id']}"
    for run, status in [(foreign, 404), (wrong_type, 409), (queued, 409)]:
        assert client.post(base + "/run", json={"expectedRevision": request["revision"], "runId": run["id"]}).status_code == status
    assert client.get(base).json()["linkedRunId"] is None
    assert client.post(base + "/request-approval", json={"expectedRevision": request["revision"]}).status_code == 409


def test_stale_revision_and_wrong_approver_are_rejected_without_side_effects(service):
    client = service
    request = assigned(client)
    client.headers["X-API-Key"] = "test-reviewer"
    request = action(client, request, "run", runId=complete_run()["id"])
    stale = request["revision"]
    request = action(client, request, "request-approval")
    base = f"/collaboration/requests/{request['id']}"
    assert client.post(base + "/decide", json={"expectedRevision": request["revision"], "decision": "approved", "reason": "不能自批"}).status_code == 403
    client.headers["X-API-Key"] = "test-approver"
    assert client.post(base + "/decide", json={"expectedRevision": stale, "decision": "approved", "reason": "旧页面"}).status_code == 409
    assert client.get(base).json()["status"] == "pending_approval"
    request = action(client, request, "decide", decision="changes_requested", reason="补充后重新提交")
    assert request["status"] == "changes_requested"
    client.headers["X-API-Key"] = "test-reviewer"
    request = action(client, request, "run", runId=complete_run()["id"])
    request = action(client, request, "request-approval")
    assert request["approvalRound"] == 2


def test_invalid_mention_and_other_recipients_notifications_are_rejected(service):
    client = service
    request = assigned(client)
    base = f"/collaboration/requests/{request['id']}"
    assert client.post(base + "/comments", json={"expectedRevision": request["revision"], "body": "合成评论", "mentions": ["outsider"]}).status_code == 422
    assert client.get(base).json()["revision"] == request["revision"]
    client.headers["X-API-Key"] = "test-reviewer"
    notification_id = client.get("/collaboration/notifications").json()["notifications"][0]["id"]
    client.headers["X-API-Key"] = "test-requester"
    assert client.post(f"/collaboration/notifications/{notification_id}/read").status_code == 404


def test_reader_cannot_create_and_payload_cannot_forge_actor(service):
    client = service
    payload = {"title": "合成事项", "contractType": "purchase"}
    assert client.post("/collaboration/requests", json={**payload, "createdBy": "admin"}).status_code == 422
    client.headers["X-API-Key"] = "test-reader"
    assert client.post("/collaboration/requests", json=payload).status_code == 403


def test_creator_can_cancel_and_cannot_reopen_terminal_request(service):
    client = service
    request = action(client, create(client), "cancel", reason="合成事项终止")
    assert request["status"] == "cancelled"
    assert client.post(f"/collaboration/requests/{request['id']}/submit", json={"expectedRevision": request["revision"]}).status_code == 409


def test_collaboration_state_changes_are_sent_to_application_integration_hook(service, monkeypatch):
    client = service
    events = []
    monkeypatch.setattr(api, "_dispatch_integration_event", lambda event: events.append(event))
    request = action(client, create(client), "submit")
    assert events and events[-1]["type"] == "request.submitted"
    assert events[-1]["tenantId"] == "acme" and events[-1]["resourceId"] == request["id"]
    assert "title" not in events[-1] and "description" not in events[-1]
