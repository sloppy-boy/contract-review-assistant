"""Run snapshots are historical evidence, never a pointer to current rules."""
import copy
import hashlib
import json
import sqlite3

import pytest

from app.review_runs import ReviewRunStore


def snapshot_payload():
    content = {
        "name": "测试采购标准", "contractType": "purchase", "jurisdiction": "CN",
        "businessScenario": "general", "effectiveScope": ["*"],
        "rules": [{
            "id": "payment-1", "categoryId": "payment_invoice", "riskType": "付款条件不明确",
            "severity": "medium", "triggerCondition": "付款条件由单方确认",
            "reviewQuestion": "付款条件是否客观？", "acceptableCondition": "双方确认期限",
            "suggestedClause": "收到合格交付物后按约付款。", "escalationPolicy": "提交法务确认",
        }],
    }
    digest = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "playbookId": "pb-test", "tenantId": "acme", "version": 1, "content": content,
        "contentHash": digest, "createdAt": "2026-09-07T00:00:00+00:00", "createdBy": "alice",
        "publishedAt": "2026-09-07T01:00:00+00:00", "publishedBy": "bob",
    }


def test_binding_survives_input_output_mutation_and_reopen(tmp_path):
    path = tmp_path / "runs.db"
    original = snapshot_payload()
    supplied = copy.deepcopy(original)
    store = ReviewRunStore(path)
    run = store.create_run(contract_type="purchase", tenant_id="acme", playbook_snapshot=supplied)
    supplied["content"]["rules"][0]["suggestedClause"] = "changed by caller"
    run["playbookSnapshot"]["content"]["rules"][0]["suggestedClause"] = "changed by reader"
    restored = ReviewRunStore(path).get_run(run["id"])
    assert restored["playbookSnapshot"] == original
    created = store.list_events(run["id"])[0]
    assert created["data"]["playbook"] == {
        "playbookId": "pb-test", "version": 1, "contentHash": original["contentHash"],
    }


def test_completed_report_uses_persisted_binding_not_pipeline_claim(tmp_path):
    store = ReviewRunStore(tmp_path / "runs.db")
    snapshot = snapshot_payload()
    run = store.create_run(contract_type="purchase", tenant_id="acme", playbook_snapshot=snapshot)
    report = {"meta": {"playbookSnapshot": {"version": 999}, "playbookApplication": "executed"}, "risks": []}
    done = store.complete_run(run["id"], report, elapsed_ms=10, stage_times=[1, 2, 3, 4])
    assert done["report"]["meta"]["playbookSnapshot"] == snapshot
    assert done["report"]["meta"]["playbookApplication"] == "snapshot_only"
    assert report["meta"]["playbookSnapshot"] == {"version": 999}
    assert done["report"]["meta"]["stageTimes"] == [1, 2, 3, 4]


def test_unbound_run_cannot_acquire_fabricated_binding_at_completion(tmp_path):
    store = ReviewRunStore(tmp_path / "runs.db")
    run = store.create_run(contract_type="purchase")
    done = store.complete_run(run["id"], {"meta": {"playbookSnapshot": snapshot_payload(), "playbookApplication": "executed"}}, elapsed_ms=0, stage_times=[0]*4)
    assert done["playbookSnapshot"] is None
    assert "playbookSnapshot" not in done["report"]["meta"]
    assert "playbookApplication" not in done["report"]["meta"]


def test_failed_run_and_history_keep_binding(tmp_path):
    store = ReviewRunStore(tmp_path / "runs.db")
    snapshot = snapshot_payload()
    run = store.create_run(contract_type="purchase", tenant_id="acme", playbook_snapshot=snapshot)
    store.update_progress(run["id"], stage=1, status="running")
    store.fail_run(run["id"], "synthetic failure")
    assert store.get_run(run["id"])["playbookSnapshot"] == snapshot
    assert store.list_runs(tenant_id="acme")[0]["playbookSnapshot"] == snapshot


@pytest.mark.parametrize("change", ["tenant", "type", "hash", "missing"])
def test_invalid_binding_never_creates_a_run(tmp_path, change):
    store = ReviewRunStore(tmp_path / "runs.db")
    snapshot = snapshot_payload()
    if change == "tenant":
        snapshot["tenantId"] = "other"
    elif change == "type":
        snapshot["content"]["contractType"] = "sale"
    elif change == "hash":
        snapshot["content"]["rules"][0]["suggestedClause"] = "tampered"
    else:
        del snapshot["publishedAt"]
    with pytest.raises(ValueError):
        store.create_run(contract_type="purchase", tenant_id="acme", playbook_snapshot=snapshot)
    assert store.list_runs(tenant_id="acme") == []


def test_legacy_database_migration_does_not_invent_historical_snapshot(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE review_runs (
            id TEXT PRIMARY KEY, contract_type TEXT NOT NULL, status TEXT NOT NULL,
            stage INTEGER NOT NULL DEFAULT 0, stage_status TEXT NOT NULL DEFAULT 'idle',
            stage_detail TEXT NOT NULL DEFAULT '', stage_times_json TEXT NOT NULL DEFAULT '[0,0,0,0]',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, report_json TEXT, error TEXT
        )""")
        conn.execute("INSERT INTO review_runs (id, contract_type, status, created_at, updated_at, report_json) VALUES ('legacy','purchase','done','then','then',?)", (json.dumps({"meta": {"legacy": True}}),))
    store = ReviewRunStore(path)
    legacy = store.get_run("legacy")
    assert legacy["playbookSnapshot"] is None
    assert legacy["report"] == {"meta": {"legacy": True}}
    assert ReviewRunStore(path).get_run("legacy")["playbookSnapshot"] is None
    new_run = store.create_run(contract_type="purchase", tenant_id="acme", playbook_snapshot=snapshot_payload())
    assert new_run["playbookSnapshot"]["version"] == 1
