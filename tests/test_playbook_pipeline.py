"""Playbook snapshot provenance through the in-process review pipeline."""

from __future__ import annotations

from copy import deepcopy

import pytest

from app.graph import run_pipeline
from app.nodes.report import report_node
from app.playbooks import PlaybookContent, PlaybookSnapshot, content_hash


def _snapshot() -> dict:
    snapshot = {
        "playbookId": "pb-purchase-cn",
        "tenantId": "acme",
        "version": 3,
        "content": {
            "name": "采购合同审查标准",
            "contractType": "purchase",
            "jurisdiction": "CN",
            "businessScenario": "general",
            "effectiveScope": ["acme"],
            "rules": [{
                "id": "payment-1", "riskType": "付款条件不明确", "severity": "medium",
                "triggerCondition": "付款条件不明确", "reviewQuestion": "付款条件是否客观？",
                "acceptableCondition": "付款条件客观", "suggestedClause": "按双方约定期限付款。",
                "escalationPolicy": "法务确认", "categoryId": "payment_invoice",
            }],
        },
        "contentHash": "sha256:test-snapshot",
        "createdAt": "2026-09-07T00:00:00+00:00",
        "createdBy": "author@example.com",
        "publishedAt": "2026-09-07T01:00:00+00:00",
        "publishedBy": "publisher@example.com",
    }
    snapshot["contentHash"] = content_hash(PlaybookContent.model_validate(snapshot["content"]))
    return snapshot


def _minimal_state(playbook_snapshot=None) -> dict:
    state = {
        "findings": [],
        "clauses": [],
        "contract_name": "contract-1",
        "contract_type": "purchase",
    }
    if playbook_snapshot is not None:
        state["playbook_snapshot"] = playbook_snapshot
    return state


def test_report_carries_a_detached_playbook_snapshot(monkeypatch):
    monkeypatch.setenv("DSH_FORCE_MOCK", "1")
    source = _snapshot()
    expected = deepcopy(source)

    report = report_node(_minimal_state(source), mode="B")["report"]

    assert report["meta"]["playbookSnapshot"] == expected
    assert report["meta"]["playbookApplication"] == "snapshot_only"

    source["content"]["name"] = "changed after report generation"
    assert report["meta"]["playbookSnapshot"] == expected

    report["meta"]["playbookSnapshot"]["content"]["name"] = "changed in report"
    assert source["content"]["name"] == "changed after report generation"


@pytest.mark.parametrize("review_mode", ["A", "B", "C"])
def test_full_mock_pipeline_transmits_playbook_snapshot(monkeypatch, review_mode):
    monkeypatch.setenv("DSH_FORCE_MOCK", "1")
    source = _snapshot()
    expected = deepcopy(source)

    report = run_pipeline(
        "第一条 标的\n本合同采购标准货物。",
        contract_type="purchase",
        contract_name="contract-1",
        review_mode=review_mode,
        playbook_snapshot=source,
    )

    assert report["meta"]["playbookSnapshot"] == expected
    assert report["meta"]["playbookApplication"] == "executed"

    source["content"]["name"] = "changed after pipeline execution"
    assert report["meta"]["playbookSnapshot"] == expected


def test_full_mock_pipeline_accepts_snapshot_model(monkeypatch):
    monkeypatch.setenv("DSH_FORCE_MOCK", "1")
    content = PlaybookContent.model_validate(_snapshot()["content"])
    snapshot = PlaybookSnapshot(
        **{
            **_snapshot(),
            "content": content,
            "contentHash": content_hash(content),
        }
    )
    expected = snapshot.model_dump(mode="json")

    report = run_pipeline(
        "第一条 标的\n本合同采购标准货物。",
        review_mode="A",
        playbook_snapshot=snapshot,
    )

    assert report["meta"]["playbookSnapshot"] == expected


def test_pipeline_without_snapshot_keeps_legacy_report_shape(monkeypatch):
    monkeypatch.setenv("DSH_FORCE_MOCK", "1")

    report = run_pipeline(
        "第一条 标的\n本合同采购标准货物。",
        review_mode="A",
    )

    assert "playbookSnapshot" not in report["meta"]
    assert "playbookApplication" not in report["meta"]


@pytest.mark.parametrize("case", ["hash", "contract_type", "forged_model"])
def test_pipeline_rejects_invalid_snapshot_before_execution(monkeypatch, case):
    monkeypatch.setenv("DSH_FORCE_MOCK", "1")
    snapshot = _snapshot()
    contract_type = "purchase"
    if case == "hash":
        snapshot["content"]["name"] = "tampered"
    elif case == "contract_type":
        contract_type = "sale"
    else:
        snapshot = PlaybookSnapshot.model_validate(snapshot).model_copy(update={"version": 0})
    with pytest.raises(ValueError):
        run_pipeline("合成测试文本", contract_type=contract_type, review_mode="A", playbook_snapshot=snapshot)


def test_pipeline_freezes_input_before_progress_callback_can_mutate_it(monkeypatch):
    monkeypatch.setenv("DSH_FORCE_MOCK", "1")
    source = _snapshot()
    expected = deepcopy(source)

    def progress(stage, status, detail=""):
        source["content"]["name"] = "changed while queued"

    report = run_pipeline("合成测试文本", review_mode="A", playbook_snapshot=source, progress=progress)
    assert report["meta"]["playbookSnapshot"] == expected
