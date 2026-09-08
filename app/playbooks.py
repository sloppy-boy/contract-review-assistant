"""Immutable Playbook domain models and content-addressed snapshots."""
from __future__ import annotations

import hashlib
import json
import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Severity = Literal["high", "medium", "low"]


def compile_trigger(condition: str) -> dict | None:
    """Parse bounded JSON predicates; legacy prose is never executable code.

    contains: literal substring in one clause; missing: literal absent from the
    full contract; number_gt/number_lt: extracted numeric field comparison;
    all/any: conditions evaluated against the same clause. No dynamic regex,
    attribute access, arithmetic expression, import, or callable is supported.
    """
    value = condition.strip()
    if not value.startswith(("{", "[")):
        return None
    if len(value) > 8192:
        raise ValueError("triggerCondition exceeds 8192 characters")
    try:
        node = json.loads(value)
    except (ValueError, RecursionError) as exc:
        raise ValueError("triggerCondition must contain valid JSON") from exc
    remaining = 64

    def validate(item, depth=0):
        nonlocal remaining
        remaining -= 1
        if remaining < 0 or depth > 6 or not isinstance(item, dict):
            raise ValueError("triggerCondition exceeds structure limits")
        op = item.get("op")
        if op in ("all", "any"):
            if set(item) != {"op", "conditions"} or not isinstance(item["conditions"], list) or not 1 <= len(item["conditions"]) <= 16:
                raise ValueError("triggerCondition all/any needs 1–16 conditions")
            for child in item["conditions"]:
                validate(child, depth + 1)
        elif op in ("contains", "missing"):
            if set(item) != {"op", "value"} or not isinstance(item["value"], str) or not item["value"].strip() or len(item["value"]) > 512:
                raise ValueError("triggerCondition text predicate needs a literal of 1–512 characters")
        elif op in ("number_gt", "number_lt"):
            if set(item) != {"op", "field", "value"} or not isinstance(item.get("field"), str) or item.get("field") not in {
                "amount", "paymentDays", "penaltyRatio", "defectDays", "warrantyMonths",
            } or type(item.get("value")) not in (int, float) or not -1e15 <= item["value"] <= 1e15 or not math.isfinite(item["value"]):
                raise ValueError("triggerCondition numeric predicate has an invalid field or value")
        else:
            raise ValueError("triggerCondition contains an unsupported operator")

    validate(node)
    return node


class PlaybookRule(BaseModel):
    """One independently addressable contract review rule."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    id: NonBlank
    riskType: NonBlank
    severity: Severity
    triggerCondition: NonBlank
    reviewQuestion: NonBlank
    acceptableCondition: NonBlank
    suggestedClause: NonBlank
    escalationPolicy: NonBlank
    categoryId: str = ""

    @field_validator("triggerCondition")
    @classmethod
    def _validate_trigger(cls, value: str) -> str:
        compile_trigger(value)
        return value


class PlaybookContent(BaseModel):
    """Versioned review policy. Empty rule sets are valid while drafting."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    name: NonBlank
    contractType: NonBlank
    jurisdiction: NonBlank
    businessScenario: NonBlank
    effectiveScope: tuple[NonBlank, ...] = Field(min_length=1)
    rules: tuple[PlaybookRule, ...] = ()

    @field_validator("rules")
    @classmethod
    def _unique_rule_ids(cls, rules: tuple[PlaybookRule, ...]) -> tuple[PlaybookRule, ...]:
        rule_ids = [rule.id for rule in rules]
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("duplicate rule id")
        return rules


class ReviewScope(BaseModel):
    """Contract context used to resolve an applicable Playbook snapshot."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    contractType: NonBlank
    jurisdiction: NonBlank
    businessScenario: NonBlank
    effectiveScope: NonBlank


def content_hash(content: PlaybookContent) -> str:
    """Return SHA-256 over deterministic UTF-8 JSON for Playbook content."""

    canonical = json.dumps(
        content.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class PlaybookSnapshot(BaseModel):
    """Immutable published evidence, verified against its content hash."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    playbookId: NonBlank
    tenantId: NonBlank
    version: int = Field(gt=0, strict=True)
    content: PlaybookContent
    contentHash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    createdAt: NonBlank
    createdBy: NonBlank
    publishedAt: NonBlank
    publishedBy: NonBlank

    @model_validator(mode="after")
    def _content_hash_matches(self) -> "PlaybookSnapshot":
        if not self.content.rules:
            raise ValueError("cannot create a published snapshot without rules")
        if self.contentHash != content_hash(self.content):
            raise ValueError("snapshot content hash mismatch")
        return self


__all__ = ["PlaybookContent", "PlaybookRule", "PlaybookSnapshot", "ReviewScope", "content_hash"]
