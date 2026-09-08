from datetime import UTC, datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.task_queue import TaskQueue


def test_queue_claim_ack_is_idempotent_and_persists_payload(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    queued = queue.enqueue("run-1", kind="review", payload={"runId": "run-1"}, max_attempts=3, now="2026-01-01T00:00:00+00:00")
    assert queued["status"] == "queued"
    claimed = queue.claim("worker-a", now="2026-01-01T00:00:00+00:00")
    assert claimed["id"] == "run-1" and claimed["attempts"] == 1
    assert queue.claim("worker-b", now="2026-01-01T00:00:00+00:00") is None
    assert queue.ack("run-1")["status"] == "succeeded"
    assert TaskQueue(tmp_path / "queue.db").get("run-1")["payload"] == {"runId": "run-1"}
    assert queue.ack("run-1")["status"] == "succeeded"


def test_failure_retries_with_backoff_then_enters_dead_letter(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    queue.enqueue("task", kind="reminder", payload={"assetId": "a"}, max_attempts=2, now="2026-01-01T00:00:00+00:00")
    queue.claim("w", now="2026-01-01T00:00:00+00:00")
    retry = queue.fail("task", "temporary", now="2026-01-01T00:00:00+00:00", delay_seconds=30)
    assert retry["status"] == "retry_wait"
    assert queue.claim("w", now="2026-01-01T00:00:29+00:00") is None
    queue.claim("w", now="2026-01-01T00:00:30+00:00")
    dead = queue.fail("task", "still broken", now="2026-01-01T00:00:30+00:00", delay_seconds=30)
    assert dead["status"] == "dead"
    assert queue.dead_letters()[0]["lastError"] == "still broken"


def test_cancel_prevents_claim_and_dead_letter_can_be_requeued(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    queue.enqueue("task", kind="review", payload={"runId": "run"})
    assert queue.cancel("task")["status"] == "cancelled"
    assert queue.claim("w") is None
    queue.enqueue("dead", kind="review", payload={"runId": "dead"}, max_attempts=1)
    queue.claim("w")
    queue.fail("dead", "bad", now="2026-01-01T00:00:00+00:00")
    retried = queue.retry_dead("dead")
    assert retried["status"] == "queued" and retried["attempts"] == 0
    assert queue.claim("w")["id"] == "dead"


def test_invalid_time_and_unknown_task_fail_closed(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    queue.enqueue("task", kind="review", payload={})
    try:
        queue.claim("w", now="not-a-date")
    except ValueError as exc:
        assert "ISO" in str(exc)
    else:
        raise AssertionError("invalid time must be rejected")
    assert queue.cancel("missing") is None


def test_stale_running_worker_is_requeued_for_recovery(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    queue.enqueue("task", kind="review", payload={}, now="2026-01-01T00:00:00+00:00")
    queue.claim("dead-worker", now="2026-01-01T00:00:00+00:00")
    recovered = queue.requeue_stale("2026-01-01T00:10:00+00:00", lease_seconds=300)
    assert recovered == ["task"]
    assert queue.claim("new-worker", now="2026-01-01T00:10:00+00:00")["workerId"] == "new-worker"


def test_queue_lists_tasks_with_status_and_kind_filters(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    queue.enqueue("review-1", kind="review", payload={})
    queue.enqueue("reminder-1", kind="reminder", payload={})
    queue.claim("worker")
    queue.fail("review-1", "broken", delay_seconds=0)
    assert [item["id"] for item in queue.list_tasks(status="retry_wait", kind="review")] == ["review-1"]
    assert [item["id"] for item in queue.list_tasks(status="queued")] == ["reminder-1"]


def test_queue_scopes_tasks_by_tenant_and_rejects_non_reference_payloads(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    queue.enqueue("acme-run", kind="review", tenant_id="acme", payload={"runId": "acme-run"})
    queue.enqueue("other-run", kind="review", tenant_id="other", payload={"runId": "other-run"})

    assert [item["id"] for item in queue.list_tasks(tenant_id="acme")] == ["acme-run"]
    assert queue.get("other-run", tenant_id="acme") is None
    with pytest.raises(ValueError, match="reference"):
        queue.enqueue("bad", kind="review", tenant_id="acme", payload={"contractText": "secret"})


def test_queue_claim_can_target_task_and_heartbeat_prevents_stale_requeue(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    queue.enqueue("first", kind="review", tenant_id="acme", payload={"runId": "first"}, now="2026-01-01T00:00:00+00:00")
    queue.enqueue("second", kind="review", tenant_id="acme", payload={"runId": "second"}, now="2026-01-01T00:00:00+00:00")
    claimed = queue.claim("worker", task_id="second", tenant_id="acme", now="2026-01-01T00:00:00+00:00")
    assert claimed["id"] == "second"
    assert queue.heartbeat("second", "worker", tenant_id="acme", now="2026-01-01T00:04:00+00:00")["updatedAt"] == "2026-01-01T00:04:00+00:00"
    assert queue.requeue_stale("2026-01-01T00:05:00+00:00", lease_seconds=300, tenant_id="acme") == []


def test_stale_worker_cannot_ack_or_fail_a_reclaimed_task(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    queue.enqueue("task", kind="review", payload={"runId": "task"}, now="2026-01-01T00:00:00+00:00")
    queue.claim("old-worker", now="2026-01-01T00:00:00+00:00")
    queue.requeue_stale("2026-01-01T00:10:00+00:00", lease_seconds=300)
    queue.claim("new-worker", now="2026-01-01T00:10:00+00:00")

    assert queue.ack("task", worker_id="old-worker") is None
    assert queue.fail("task", "late failure", worker_id="old-worker") is None
    assert queue.ack("task", worker_id="new-worker")["status"] == "succeeded"


def test_concurrent_claim_has_one_winner_and_preserves_tenant_boundary(tmp_path):
    queue = TaskQueue(tmp_path / "queue.db")
    queue.enqueue("task", kind="review", tenant_id="acme", payload={"runId": "task"})

    def claim(worker):
        return queue.claim(worker, tenant_id="acme")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(claim, [f"worker-{i}" for i in range(8)]))
    winners = [item for item in results if item is not None]
    assert len(winners) == 1 and winners[0]["tenantId"] == "acme"
    assert queue.claim("other-tenant", tenant_id="other") is None
