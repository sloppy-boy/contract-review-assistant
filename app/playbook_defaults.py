"""Deterministic built-in Playbook derived from the existing risk matrix."""
from __future__ import annotations

from .legal.risk_matrix import RISK_CATEGORIES
from .playbooks import PlaybookContent, PlaybookRule, PlaybookSnapshot, content_hash


SYSTEM_BASELINE_PLAYBOOK_ID = "system-risk-matrix-baseline"
_SYSTEM_ACTOR = "system:risk-matrix"
_SYSTEM_PUBLISHED_AT = "2026-09-07T00:00:00+00:00"


def built_in_playbook_snapshot(contract_type: str, *, tenant_id: str) -> PlaybookSnapshot:
    """Return the immutable version-1 snapshot used when uploads omit a selection.

    The legacy matrix supplies checks and severity rubrics, but does not contain
    legally reviewed replacement clauses. The adapter preserves that boundary in
    the generated rule text instead of inventing policy.
    """

    rules = tuple(
        PlaybookRule(
            id=f"{category.id}-{rubric_index}",
            categoryId=category.id,
            riskType=category.name,
            severity=rubric.severity,
            triggerCondition=rubric.condition,
            reviewQuestion="\n".join(category.checklist),
            acceptableCondition="未触发该风险条件，并由法务结合审查清单确认。",
            suggestedClause="内置风险矩阵未提供经法务核验的替代条款，需法务确认。",
            escalationPolicy="命中本规则时需法务人工复核。",
        )
        for category in RISK_CATEGORIES
        for rubric_index, rubric in enumerate(category.rubric, start=1)
    )
    content = PlaybookContent(
        name="system baseline",
        contractType=contract_type,
        jurisdiction="CN",
        businessScenario="general",
        effectiveScope=("*",),
        rules=rules,
    )
    digest = content_hash(content)
    return PlaybookSnapshot(
        # Code upgrades or contract-type changes cannot rewrite an existing
        # built-in identity. Its full content remains embedded in each run.
        playbookId=f"{SYSTEM_BASELINE_PLAYBOOK_ID}-{digest}",
        tenantId=tenant_id,
        version=1,
        content=content,
        contentHash=digest,
        createdAt=_SYSTEM_PUBLISHED_AT,
        createdBy=_SYSTEM_ACTOR,
        publishedAt=_SYSTEM_PUBLISHED_AT,
        publishedBy=_SYSTEM_ACTOR,
    )


__all__ = ["SYSTEM_BASELINE_PLAYBOOK_ID", "built_in_playbook_snapshot"]
