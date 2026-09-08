"""Deterministic policy checks from the frozen snapshot, separate from LLM review.

Rule matching establishes a company-policy signal, never a legal conclusion.
Natural-language historical rules remain compatible but yield only uncertain
manual-review hints when a meaningful phrase occurs in the contract.
"""
from __future__ import annotations

import hashlib
import math
import re

from .playbooks import PlaybookSnapshot, compile_trigger
from .state import ClauseFact, ContractState


def _evaluate(node: dict, clause: ClauseFact, contract_text: str):
    op = node["op"]
    if op in ("all", "any"):
        results = [_evaluate(child, clause, contract_text) for child in node["conditions"]]
        matched = all(r[0] for r in results) if op == "all" else any(r[0] for r in results)
        supporting = [r for r in results if r[0]]
        return (matched, "；".join(r[1] for r in supporting),
                min((r[2] for r in supporting), default=0.0), any(r[3] for r in supporting))
    if op == "contains":
        return node["value"] in clause.quote, f"条款原文包含「{node['value']}」", 0.99, True
    if op == "missing":
        return (node["value"] not in contract_text, f"合同全文未检出「{node['value']}」；需人工检查同义表述及附件", 0.6, False)
    value = clause.keyNumbers.get(node["field"])
    if type(value) not in (int, float) or not math.isfinite(value):
        # A missing extracted number does not prove the threshold is violated.
        return False, "缺少可比较的数值", 0.0, False
    matched = value > node["value"] if op == "number_gt" else value < node["value"]
    return matched, f"抽取事实 {node['field']}={value}，规则 {op} {node['value']}（需核对数值语义）", 0.7, True


def _legacy_terms(rule) -> tuple[str, ...]:
    # Fixed separator regex only; no regex can be provided by a Playbook author.
    parts = re.split(r"[\s，。；：、？?：:（）()]+", rule.riskType + "\n" + rule.reviewQuestion)
    stopwords = {"合同", "条款", "风险", "是否", "审查", "应当", "必须", "双方", "约定", "确认"}
    return tuple(dict.fromkeys(p for p in parts if 2 <= len(p) <= 80 and p not in stopwords))


def execute_playbook(snapshot: PlaybookSnapshot | dict, clauses: list[ClauseFact], contract_text: str) -> list[dict]:
    """Return Finding-compatible dictionaries with complete immutable provenance."""
    raw = snapshot.model_dump(mode="json") if isinstance(snapshot, PlaybookSnapshot) else snapshot
    book = PlaybookSnapshot.model_validate(raw)
    findings = []
    for rule in book.content.rules:
        predicate = compile_trigger(rule.triggerCondition)
        absence_emitted = False
        for clause in clauses or [ClauseFact(clauseId="contract", quote="")]:
            if predicate is None:
                matches = [term for term in _legacy_terms(rule) if term in clause.quote]
                if not matches:
                    continue
                matched, evidence, confidence, uses_quote = True, f"原文包含「{matches[0]}」；自然语言触发条件未经自动判定，仅供人工确认", 0.35, True
            else:
                matched, evidence, confidence, uses_quote = _evaluate(predicate, clause, contract_text)
            if not matched or (not uses_quote and absence_emitted):
                continue
            if not uses_quote:
                absence_emitted = True
            quote = clause.quote if uses_quote else ""
            offset = contract_text.find(quote) if quote else -1
            verified = offset >= 0
            if quote and not verified:
                confidence = min(confidence, 0.35)
                evidence += "；抽取引文未能在原文精确定位，需人工核对"
            location = f"字符 {offset + 1}–{offset + len(quote)}" if verified else "全文（缺失检查）" if not quote else "原文位置待核对"
            clause_id = clause.clauseId if quote else "contract"
            provenance = {"ruleId": rule.id, "playbookId": book.playbookId,
                          "version": book.version, "contentHash": book.contentHash,
                          "execution": "declarative" if predicate is not None else "manual_hint"}
            digest = hashlib.sha256(f"{book.playbookId}:{book.version}:{rule.id}:{clause_id}:{quote}".encode()).hexdigest()[:20]
            findings.append({
                "id": f"pb-{digest}", "worker": rule.categoryId or "playbook", "source": "playbook",
                **{k: provenance[k] for k in ("ruleId", "playbookId", "version", "contentHash")},
                "sourceDetails": provenance, "clauseId": clause_id, "clauseQuote": quote,
                "location": location, "paragraphId": "",
                "riskType": rule.riskType, "severity": rule.severity,
                "legalBasis": {"tier": "none", "articleId": "", "version": "", "quote": ""},
                "evidence": evidence, "confidence": confidence, "requiresHumanReview": True,
                "suggestion": f"{rule.reviewQuestion} 可接受条件：{rule.acceptableCondition}",
                "suggestionClauseText": rule.suggestedClause,
                "escalationRecommendation": rule.escalationPolicy, "status": "proposed",
            })
    return findings


def playbook_node(state: ContractState) -> dict:
    snapshot = state.get("playbook_snapshot")
    if snapshot is None:
        return {"playbook_findings": []}
    raw = snapshot.model_dump(mode="json") if isinstance(snapshot, PlaybookSnapshot) else snapshot
    book = PlaybookSnapshot.model_validate(raw)
    declarative = sum(compile_trigger(rule.triggerCondition) is not None for rule in book.content.rules)
    return {
        "playbook_findings": execute_playbook(book, state.get("clauses", []), state.get("contract_text", "")),
        "playbook_execution": {"application": "executed", "declarativeRules": declarative,
                               "manualRules": len(book.content.rules) - declarative},
    }
