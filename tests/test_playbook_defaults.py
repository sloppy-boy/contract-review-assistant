from app.legal.risk_matrix import RISK_CATEGORIES
from app.playbook_defaults import (
    SYSTEM_BASELINE_PLAYBOOK_ID,
    built_in_playbook_snapshot,
)
from app.playbooks import content_hash


def test_builtin_snapshot_preserves_every_risk_matrix_rubric_and_checklist():
    snapshot = built_in_playbook_snapshot("purchase", tenant_id="acme")

    assert snapshot.playbookId.startswith(SYSTEM_BASELINE_PLAYBOOK_ID)
    assert snapshot.tenantId == "acme"
    assert snapshot.version == 1
    assert snapshot.content.name == "system baseline"
    assert snapshot.content.contractType == "purchase"
    assert snapshot.content.jurisdiction == "CN"
    assert snapshot.content.businessScenario == "general"
    assert snapshot.content.effectiveScope == ("*",)
    assert snapshot.contentHash == content_hash(snapshot.content)

    expected_rubrics = {
        (category.id, rubric.condition, rubric.severity)
        for category in RISK_CATEGORIES
        for rubric in category.rubric
    }
    actual_rubrics = {
        (rule.categoryId, rule.triggerCondition, rule.severity)
        for rule in snapshot.content.rules
    }
    assert actual_rubrics == expected_rubrics

    for category in RISK_CATEGORIES:
        category_rules = [rule for rule in snapshot.content.rules if rule.categoryId == category.id]
        assert category_rules
        for checklist_item in category.checklist:
            assert all(checklist_item in rule.reviewQuestion for rule in category_rules)


def test_builtin_snapshot_is_deterministic_and_contract_type_specific():
    purchase = built_in_playbook_snapshot("purchase", tenant_id="acme")
    repeated = built_in_playbook_snapshot("purchase", tenant_id="acme")
    sale = built_in_playbook_snapshot("sale", tenant_id="acme")

    assert purchase == repeated
    assert purchase.createdAt == purchase.publishedAt
    assert purchase.createdBy == "system:risk-matrix"
    assert purchase.publishedBy == "system:risk-matrix"
    assert sale.content.contractType == "sale"
    assert sale.contentHash != purchase.contentHash
    assert sale.playbookId != purchase.playbookId


def test_matrix_change_never_reuses_same_published_identity(monkeypatch):
    from dataclasses import replace
    from app import playbook_defaults

    original = built_in_playbook_snapshot("purchase", tenant_id="acme")
    categories = list(RISK_CATEGORIES)
    categories[0] = replace(categories[0], checklist=("New check for a changed baseline.",))
    monkeypatch.setattr(playbook_defaults, "RISK_CATEGORIES", categories)
    changed = built_in_playbook_snapshot("purchase", tenant_id="acme")
    assert changed.playbookId != original.playbookId
    assert changed.contentHash != original.contentHash
