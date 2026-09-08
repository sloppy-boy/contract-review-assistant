"""Behavioral checks for frozen, bounded declarative Playbook rules."""
import json

import pytest

from app.graph import run_pipeline
from app.playbooks import PlaybookContent, PlaybookRule, PlaybookSnapshot, content_hash


def make_snapshot(trigger, **changes):
    rule = dict(id="payment-cap", riskType="企业付款期限超限", severity="medium",
                triggerCondition=json.dumps(trigger, ensure_ascii=False) if isinstance(trigger, dict) else trigger,
                reviewQuestion="付款期限是否超过内部标准？", acceptableCondition="付款不超过60天",
                suggestedClause="验收后60日内付款。", escalationPolicy="提交采购法务确认",
                categoryId="payment_invoice")
    rule.update(changes)
    content = PlaybookContent(name="合成采购标准", contractType="purchase", jurisdiction="CN",
                              businessScenario="general", effectiveScope=("*",), rules=(PlaybookRule(**rule),))
    return PlaybookSnapshot(playbookId="pb-synthetic", tenantId="acme", version=2, content=content,
                            contentHash=content_hash(content), createdAt="2026-09-08", createdBy="author",
                            publishedAt="2026-09-08", publishedBy="reviewer")


@pytest.mark.parametrize("mode", ["A", "B", "C"])
def test_frozen_rule_changes_actual_report_and_carries_evidence(monkeypatch, mode):
    monkeypatch.setenv("DSH_FORCE_MOCK", "1")
    snapshot = make_snapshot({"op": "contains", "value": "验收后120日"})
    text = "第一条 付款\n采购方在验收后120日内付款。"
    report = run_pipeline(text, review_mode=mode, playbook_snapshot=snapshot)
    rules = [risk for risk in report["risks"] if risk.get("source") == "playbook"]
    assert len(rules) == 1
    risk = rules[0]
    assert risk["ruleId"] == "payment-cap"
    assert risk["sourceDetails"]["contentHash"] == snapshot.contentHash
    assert risk["sourceDetails"]["version"] == 2
    assert "验收后120日" in risk["clauseQuote"]
    assert risk["location"]
    assert risk["evidence"]
    assert risk["confidence"] >= 0.9
    assert risk["requiresHumanReview"]  # company policy is not a legal conclusion
    assert risk["suggestionClauseText"] == "验收后60日内付款。"
    assert report["meta"]["playbookApplication"] == "executed"
    clean = run_pipeline("第一条 付款\n采购方在验收后30日内付款。", review_mode=mode, playbook_snapshot=snapshot)
    assert not [risk for risk in clean["risks"] if risk.get("source") == "playbook"]


@pytest.mark.parametrize("trigger", [
    '{"op":"regex","value":"(a+)+$"}', '{"op":"exec","value":"import os"}',
    '{"op":"contains","value":"付款","extra":"ignored"}', '{bad json',
    '{"op":"number_gt","field":"__class__","value":3}',
    '{"op":"contains","value":""}', '{"op":"all","conditions":[]}',
    '{"op":"number_gt","field":[],"value":3}',
    '{"op":"number_gt","field":"paymentDays","value":1e300}',
])
def test_invalid_declarative_conditions_rejected_when_rules_are_created(trigger):
    with pytest.raises(ValueError, match="triggerCondition"):
        make_snapshot(trigger)


def test_missing_contract_evidence_is_reported_without_fabricated_quote(monkeypatch):
    monkeypatch.setenv("DSH_FORCE_MOCK", "1")
    snapshot = make_snapshot({"op": "missing", "value": "数据删除"})
    report = run_pipeline("第一条 标的\n供应商提供软件服务。", review_mode="B", playbook_snapshot=snapshot)
    risks = [r for r in report["risks"] if r.get("source") == "playbook"]
    assert len(risks) == 1
    assert risks[0]["clauseQuote"] == ""
    assert risks[0]["confidence"] < 0.8
    assert risks[0]["requiresHumanReview"]
    assert "未检出" in risks[0]["evidence"]


def test_unstructured_rule_requires_related_text_and_remains_uncertain(monkeypatch):
    monkeypatch.setenv("DSH_FORCE_MOCK", "1")
    snapshot = make_snapshot("需要法务判断", riskType="付款期限")
    related = run_pipeline("第一条 付款期限\n付款期限由双方另行确定。", review_mode="A", playbook_snapshot=snapshot)
    hints = [r for r in related["risks"] if r.get("source") == "playbook"]
    assert len(hints) == 1
    assert hints[0]["confidence"] < 0.5
    assert hints[0]["requiresHumanReview"]
    unrelated = run_pipeline("第一条 标的\n本合同采购普通货物。", review_mode="A", playbook_snapshot=snapshot)
    assert not [r for r in unrelated["risks"] if r.get("source") == "playbook"]


def test_all_conditions_require_same_clause_and_freeze_before_callbacks(monkeypatch):
    monkeypatch.setenv("DSH_FORCE_MOCK", "1")
    snapshot = make_snapshot({"op": "all", "conditions": [
        {"op": "contains", "value": "付款"},
        {"op": "number_gt", "field": "paymentDays", "value": 60},
    ]}).model_dump(mode="json")

    def mutate(*args):
        snapshot["content"]["rules"][0]["triggerCondition"] = '{"op":"contains","value":"不存在"}'

    report = run_pipeline("第一条 付款\n验收后90日内付款。", review_mode="C", playbook_snapshot=snapshot, progress=mutate)
    rules = [r for r in report["risks"] if r.get("source") == "playbook"]
    assert len(rules) == 1
    assert "90" in rules[0]["evidence"]
    source = make_snapshot({"op": "all", "conditions": [
        {"op": "contains", "value": "付款"}, {"op": "contains", "value": "不得退款"},
    ]})
    no_match = run_pipeline("第一条 付款\n货物验收后付款。\n第二条 订金\n订金不得退款。", review_mode="A", playbook_snapshot=source)
    assert not [r for r in no_match["risks"] if r.get("source") == "playbook"]


def test_expression_depth_and_size_are_bounded():
    node = {"op": "contains", "value": "付款"}
    for _ in range(20):
        node = {"op": "all", "conditions": [node]}
    with pytest.raises(ValueError, match="triggerCondition"):
        make_snapshot(node)
    with pytest.raises(ValueError, match="triggerCondition"):
        make_snapshot({"op": "contains", "value": "a" * 9000})


def test_mock_worker_identifies_baseline_source(monkeypatch):
    monkeypatch.setenv("DSH_FORCE_MOCK", "1")
    report = run_pipeline("第一条 违约责任\n违约金为合同金额的50%。", review_mode="A")
    assert report["risks"]
    assert all(r["source"] == "rule" for r in report["risks"])


def test_merging_identical_baseline_and_policy_risk_retains_both_origins(monkeypatch):
    monkeypatch.setenv("DSH_FORCE_MOCK", "1")
    snapshot = make_snapshot({"op": "contains", "value": "50%"}, riskType="违约金过高", severity="high")
    report = run_pipeline("第一条 违约责任\n违约金为合同金额的50%。", review_mode="A", playbook_snapshot=snapshot)
    risks = [r for r in report["risks"] if r["riskType"] == "违约金过高"]
    assert len(risks) == 1
    assert {s["source"] for s in risks[0]["sources"]} == {"rule", "playbook"}
    policy = next(s for s in risks[0]["sources"] if s["source"] == "playbook")
    assert policy["contentHash"] == snapshot.contentHash
    assert policy["evidence"]
