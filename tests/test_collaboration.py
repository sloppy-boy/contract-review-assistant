from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sqlite3
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.security import Principal
from app import collaboration


CREATOR = Principal("requester", "acme", "requester")
REVIEWER = Principal("reviewer", "acme", "legal_reviewer")
APPROVER = Principal("approver", "acme", "legal_reviewer")
ADMIN = Principal("admin", "acme", "admin")
READER = Principal("reader", "acme", "reader")
OTHER = Principal("requester", "other", "admin")
NOW = datetime(2026, 9, 7, 0, 0, tzinfo=UTC)


def setup_request(tmp_path):
    module = collaboration
    store = module.CollaborationStore(tmp_path / "collaboration.db", clock=lambda: NOW)
    request = store.create(module.RequestContent(title="采购合同", contractType="purchase"), actor=CREATOR)
    return store, request


def in_review(tmp_path):
    store, request = setup_request(tmp_path)
    request = store.submit(request["id"], actor=CREATOR, expected_revision=request["revision"])
    request = store.assign(request["id"], actor=CREATOR, expected_revision=request["revision"],
                           assignee="reviewer", approver="approver", due_at="2026-09-08T08:00:00+08:00")
    return store, request


def run(run_id="run-1", **changes):
    return {"id": run_id, "tenantId": "acme", "contract_type": "purchase", "status": "done",
            "report": {"summary": {"level": "high"}, "meta": {"playbookSnapshot": {"version": 1}}}, **changes}


def pending(tmp_path):
    store, request = in_review(tmp_path)
    report_run = run()
    request = store.bind_run(request["id"], actor=REVIEWER, expected_revision=request["revision"], run=report_run)
    request = store.request_approval(request["id"], actor=REVIEWER, expected_revision=request["revision"],
                                     run=report_run, dispositions={"finding-1": {"decision": "accepted", "reason": "verified"}})
    return store, request


@pytest.mark.parametrize("changes", [{"title": " "}, {"title": "x" * 201}, {"contractType": " "}, {"description": "x" * 10001}])
def test_invalid_content_is_rejected(changes):
    with pytest.raises(ValidationError):
        collaboration.RequestContent(**{"title": "purchase", "contractType": "purchase", **changes})


def test_creation_scoping_and_reopen(tmp_path):
    store, request = setup_request(tmp_path)
    assert request["status"] == "draft" and request["revision"] == 1
    assert request["createdBy"] == "requester" and request["description"] == ""
    assert request["approvalRound"] == 0 and request["approvalSnapshot"] is None
    assert request["dueAt"] is None and request["isOverdue"] is False
    assert store.get(request["id"], actor=OTHER) is None
    assert store.list_requests(actor=OTHER) == []
    assert store.get(request["id"], actor=READER)["id"] == request["id"]
    reopened = collaboration.CollaborationStore(store.path, clock=lambda: NOW)
    assert reopened.get(request["id"], actor=CREATOR) == request
    assert [event["type"] for event in reopened.events(request["id"], actor=CREATOR)] == ["created"]
    with pytest.raises(PermissionError):
        store.create(collaboration.RequestContent(title="x", contractType="purchase"), actor=READER)


def test_terminal_collaboration_requests_can_be_retained_without_exposing_body(tmp_path):
    store, request = setup_request(tmp_path)
    request = store.cancel(request["id"], actor=CREATOR, expected_revision=1, reason="重复提交")
    candidates = store.retention_candidates(tenant_id="acme", before="2999-01-01T00:00:00+00:00")
    assert candidates == [{"resourceType": "collaboration_request", "resourceId": request["id"], "tenantId": "acme", "createdAt": request["createdAt"], "status": "cancelled"}]
    assert store.purge_request(request["id"], tenant_id="acme") is True
    assert store.get(request["id"], actor=CREATOR) is None
    assert store.purge_request(request["id"], tenant_id="acme") is False


def test_create_revalidates_unchecked_model_copy(tmp_path):
    store, request = setup_request(tmp_path)
    forged = collaboration.RequestContent(title="valid", contractType="purchase").model_copy(update={"title": " "})
    with pytest.raises(ValidationError):
        store.create(forged, actor=CREATOR)
    assert [item["id"] for item in store.list_requests(actor=CREATOR)] == [request["id"]]


def test_approval_freezes_report_and_dispositions_at_submission_time(tmp_path):
    store, request = in_review(tmp_path)
    report_run = run()
    request = store.bind_run(request["id"], actor=REVIEWER, expected_revision=3, run=report_run)
    report_run["report"]["summary"]["level"] = "low"
    dispositions = {"f1": {"decision": "accepted", "reason": "current report checked"}}
    request = store.request_approval(request["id"], actor=REVIEWER, expected_revision=4,
                                     run=report_run, dispositions=dispositions)
    assert request["approvalSnapshot"]["report"]["summary"]["level"] == "low"
    assert request["approvalSnapshot"]["dispositions"]["f1"]["reason"] == "current report checked"
    report_run["report"]["summary"]["level"] = "changed after submission"
    dispositions["f1"]["reason"] = "changed after submission"
    snapshot = store.get(request["id"], actor=CREATOR)["approvalSnapshot"]
    assert snapshot["report"]["summary"]["level"] == "low"
    assert snapshot["dispositions"]["f1"]["reason"] == "current report checked"


def test_submit_assign_sla_and_notifications(tmp_path):
    store, request = setup_request(tmp_path)
    request = store.submit(request["id"], actor=CREATOR, expected_revision=1, recipients=["reviewer", "reviewer", "requester"])
    assert request["status"] == "submitted"
    assert len(store.notifications(actor=REVIEWER)) == 1
    assert store.notifications(actor=CREATOR) == []
    request = store.assign(request["id"], actor=CREATOR, expected_revision=2, assignee="reviewer",
                           approver="approver", due_at="2026-09-08T08:00:00+08:00")
    assert request["dueAt"] == "2026-09-08T00:00:00+00:00"
    assert request["status"] == "in_review" and request["assignee"] == "reviewer"
    later = collaboration.CollaborationStore(store.path, clock=lambda: NOW + timedelta(days=2))
    assert later.get(request["id"], actor=CREATOR)["isOverdue"] is True
    assert later.list_requests(actor=CREATOR, status="in_review")[0]["isOverdue"] is True
    assert later.list_requests(actor=CREATOR, status="draft") == []


@pytest.mark.parametrize("due", ["2026-09-08T00:00:00", "2026-09-06T00:00:00Z", "2026-09-07T00:00:00Z", "invalid"])
def test_assignment_rejects_naive_past_or_invalid_deadline(tmp_path, due):
    store, request = setup_request(tmp_path)
    request = store.submit(request["id"], actor=CREATOR, expected_revision=1)
    with pytest.raises(ValueError):
        store.assign(request["id"], actor=CREATOR, expected_revision=2,
                     assignee="reviewer", approver="approver", due_at=due)
    assert store.get(request["id"], actor=CREATOR) == request


def test_snapshot_is_detached_and_all_approval_rounds_survive(tmp_path):
    store, request = in_review(tmp_path)
    report_run = run()
    request = store.bind_run(request["id"], actor=REVIEWER, expected_revision=3, run=report_run)
    dispositions = {"f1": {"decision": "accepted", "reason": "checked"}}
    request = store.request_approval(request["id"], actor=REVIEWER, expected_revision=4, run=report_run, dispositions=dispositions)
    report_run["report"]["summary"]["level"] = "low"
    dispositions["f1"]["reason"] = "tampered"
    request["approvalSnapshot"]["report"]["summary"]["level"] = "tampered"
    frozen = store.get(request["id"], actor=CREATOR)["approvalSnapshot"]
    assert frozen["report"]["summary"]["level"] == "high"
    assert frozen["dispositions"]["f1"]["reason"] == "checked"
    assert frozen["report"]["meta"]["playbookSnapshot"]["version"] == 1
    request = store.decide(request["id"], actor=APPROVER, expected_revision=5, decision="changes_requested", reason="补充材料")
    assert request["status"] == "changes_requested"
    request = store.bind_run(request["id"], actor=REVIEWER, expected_revision=6, run=run("run-2"))
    request = store.request_approval(request["id"], actor=REVIEWER, expected_revision=7, run=run("run-2"), dispositions={})
    assert request["approvalRound"] == 2
    request = store.decide(request["id"], actor=APPROVER, expected_revision=8, decision="approved", reason="同意")
    later = collaboration.CollaborationStore(store.path, clock=lambda: NOW + timedelta(days=2))
    assert later.get(request["id"], actor=CREATOR)["isOverdue"] is False
    rounds = [event for event in later.events(request["id"], actor=READER) if event["type"] == "approval_requested"]
    assert [event["data"]["approvalRound"] for event in rounds] == [1, 2]
    assert rounds[0]["data"]["approvalSnapshot"] == frozen
    assert rounds[1]["data"]["approvalSnapshot"]["runId"] == "run-2"


@pytest.mark.parametrize("changes", [{"tenantId": "other"}, {"contract_type": "lease"}, {"status": "queued"}, {"report": None}, {"id": ""}])
def test_report_link_rejects_ineligible_runs_without_effects(tmp_path, changes):
    store, request = in_review(tmp_path)
    before = store.events(request["id"], actor=CREATOR)
    with pytest.raises(ValueError):
        store.bind_run(request["id"], actor=REVIEWER, expected_revision=3, run=run(**changes))
    assert store.events(request["id"], actor=CREATOR) == before
    assert store.get(request["id"], actor=CREATOR) == request


def test_approval_requires_matching_link_and_assignment(tmp_path):
    store, request = in_review(tmp_path)
    with pytest.raises(ValueError):
        store.request_approval(request["id"], actor=REVIEWER, expected_revision=3, run=run(), dispositions={})
    request = store.bind_run(request["id"], actor=ADMIN, expected_revision=3, run=run())
    with pytest.raises(ValueError):
        store.request_approval(request["id"], actor=REVIEWER, expected_revision=4, run=run("different"), dispositions={})


def test_permissions_roles_and_tenant_precede_version_errors(tmp_path):
    store, request = pending(tmp_path)
    for actor in [CREATOR, REVIEWER, ADMIN, Principal("approver", "acme", "reader")]:
        with pytest.raises(PermissionError):
            store.decide(request["id"], actor=actor, expected_revision=5, decision="approved", reason="yes")
    for method, arguments in [
        (store.submit, {}),
        (store.assign, {"assignee": "reviewer", "approver": "approver", "due_at": "2026-09-08T00:00:00Z"}),
        (store.bind_run, {"run": run()}),
        (store.request_approval, {"run": run(), "dispositions": {}}),
        (store.decide, {"decision": "approved", "reason": "yes"}),
        (store.cancel, {"reason": "yes"}),
        (store.comment, {"body": "x", "mentions": []}),
    ]:
        with pytest.raises(KeyError):
            method(request["id"], actor=OTHER, expected_revision=-1, **arguments)
    with pytest.raises(KeyError):
        store.events(request["id"], actor=OTHER)
    assert store.get(request["id"], actor=CREATOR) == request


def test_only_creator_or_admin_can_submit_assign_cancel(tmp_path):
    store, request = setup_request(tmp_path)
    with pytest.raises(PermissionError):
        store.submit(request["id"], actor=REVIEWER, expected_revision=1)
    request = store.submit(request["id"], actor=ADMIN, expected_revision=1)
    with pytest.raises(PermissionError):
        store.assign(request["id"], actor=REVIEWER, expected_revision=2,
                     assignee="reviewer", approver="approver", due_at="2026-09-08T00:00:00Z")
    with pytest.raises(PermissionError):
        store.cancel(request["id"], actor=REVIEWER, expected_revision=2, reason="no")
    with pytest.raises(ValueError):
        store.cancel(request["id"], actor=CREATOR, expected_revision=2, reason=" ")
    request = store.cancel(request["id"], actor=ADMIN, expected_revision=2, reason="duplicate")
    assert request["status"] == "cancelled"
    with pytest.raises(ValueError):
        store.submit(request["id"], actor=CREATOR, expected_revision=3)


@pytest.mark.parametrize("actor", [CREATOR, Principal("reviewer", "acme", "reader"), APPROVER])
def test_binding_requires_current_assignee_or_admin_and_reviewer_role(tmp_path, actor):
    store, request = in_review(tmp_path)
    with pytest.raises(PermissionError):
        store.bind_run(request["id"], actor=actor, expected_revision=3, run=run())
    with pytest.raises(PermissionError):
        store.request_approval(request["id"], actor=actor, expected_revision=3, run=run(), dispositions={})


def test_concurrent_stale_revision_commits_exactly_one_comment(tmp_path):
    store, request = in_review(tmp_path)
    def write(body):
        try:
            return store.comment(request["id"], actor=CREATOR, expected_revision=3, body=body, mentions=[])
        except ValueError:
            return None
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(write, ["first", "second"]))
    assert sum(result is not None for result in outcomes) == 1
    assert store.get(request["id"], actor=CREATOR)["revision"] == 4
    assert len([e for e in store.events(request["id"], actor=CREATOR) if e["type"] == "commented"]) == 1
    assert len([n for n in store.notifications(actor=APPROVER) if n["type"] == "commented"]) == 1


def test_comment_mentions_notifications_isolation_and_idempotent_read(tmp_path):
    store, request = in_review(tmp_path)
    request = store.comment(request["id"], actor=CREATOR, expected_revision=3, body="请查看 @reader", mentions=["reader", "reviewer", "reader"])
    event = store.events(request["id"], actor=CREATOR)[-1]
    assert event["subject"] == "requester" and event["data"]["body"] == "请查看 @reader"
    assert event["data"]["mentions"] == ["reader", "reviewer"]
    notes = store.notifications(actor=READER, unread_only=True)
    assert len(notes) == 1 and notes[0]["requestId"] == request["id"]
    assert store.notifications(actor=OTHER) == []
    with pytest.raises(KeyError):
        store.mark_read(notes[0]["id"], actor=OTHER)
    with pytest.raises(KeyError):
        store.mark_read(notes[0]["id"], actor=APPROVER)
    marked = store.mark_read(notes[0]["id"], actor=READER)
    assert marked["readAt"] == NOW.isoformat()
    assert store.mark_read(notes[0]["id"], actor=READER) == marked
    assert store.notifications(actor=READER, unread_only=True) == []
    with pytest.raises(PermissionError):
        store.comment(request["id"], actor=READER, expected_revision=4, body="write", mentions=[])
    with pytest.raises(ValueError):
        store.comment(request["id"], actor=CREATOR, expected_revision=4, body=" ", mentions=[])


def test_finalized_workflow_rejects_mutation_but_allows_followup_comment(tmp_path):
    store, request = pending(tmp_path)
    with pytest.raises(ValueError):
        store.decide(request["id"], actor=APPROVER, expected_revision=5, decision="approved", reason=" ")
    with pytest.raises(ValueError):
        store.decide(request["id"], actor=APPROVER, expected_revision=5, decision="draft", reason="invalid")
    request = store.decide(request["id"], actor=APPROVER, expected_revision=5, decision="approved", reason="approved")
    for method, arguments, actor in [
        (store.cancel, {"reason": "no"}, CREATOR),
        (store.assign, {"assignee": "reviewer", "approver": "approver", "due_at": "2026-09-08T00:00:00Z"}, CREATOR),
        (store.bind_run, {"run": run()}, REVIEWER),
        (store.request_approval, {"run": run(), "dispositions": {}}, REVIEWER),
        (store.decide, {"decision": "changes_requested", "reason": "no"}, APPROVER),
    ]:
        with pytest.raises(ValueError):
            method(request["id"], actor=actor, expected_revision=6, **arguments)
    assert store.comment(request["id"], actor=CREATOR, expected_revision=6, body="archived copy received", mentions=[])["status"] == "approved"


def test_notification_failure_rolls_back_request_and_event(tmp_path):
    store, request = setup_request(tmp_path)
    with sqlite3.connect(store.path) as conn:
        conn.execute("CREATE TRIGGER fail_notification BEFORE INSERT ON collaboration_notifications BEGIN SELECT RAISE(ABORT, 'injected storage failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.submit(request["id"], actor=CREATOR, expected_revision=1, recipients=["reviewer"])
    assert store.get(request["id"], actor=CREATOR) == request
    assert len(store.events(request["id"], actor=CREATOR)) == 1
    assert store.notifications(actor=REVIEWER) == []
