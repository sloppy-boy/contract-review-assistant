"""报告生成节点（SPEC 2.3 ④）：分级汇总 + 按严重度排序 + 统一 JSON schema。

- 有复核配置（B/C）时只读 status ∈ {upheld, disputed}；
  无复核配置（消融 A 档）时所有 proposed 直接视为通过进报告（否则 A 档报告为空，消融无法对比）。
- 报告 schema（ReportOut）一份结构三处消费：评测脚本 / 前端 / Word 导出。
- clauses 全量输出（前端条款导航 + 干净组误报密度分母）。
"""
from __future__ import annotations

from copy import deepcopy
import time

from ..config import REVIEW_MODE, using_mock
from ..legal.corpus_snapshot import LegalCorpusSnapshot
from ..state import ContractState, ReportOut
from ..report_evidence import normalize_report

_SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}


def report_node(state: ContractState, mode: str | None = None) -> dict:
    """④ 报告生成：写 state['report']（dict，可 JSON 序列化）。

    mode 由 graph 注入（与消融档位一致）；None 时回退 config.REVIEW_MODE。
    """
    start = time.time()
    findings = state.get("findings", [])
    clauses = state.get("clauses", [])
    mode = (mode or REVIEW_MODE).upper()

    if mode == "A":
        effective = [f for f in findings if f.status in ("proposed", "upheld", "disputed")]
    else:
        effective = [f for f in findings if f.status in ("upheld", "disputed")]

    # 分级汇总
    counts = {"high": 0, "medium": 0, "low": 0}
    by_category: dict[str, int] = {}
    for f in effective:
        counts[f.severity] = counts.get(f.severity, 0) + 1
        by_category[f.worker] = by_category.get(f.worker, 0) + 1

    # 按严重度排序（high → medium → low；同严重度按条款号）
    risks = sorted(
        effective,
        key=lambda f: (_SEVERITY_ORDER.get(f.severity, 9), f.clauseId),
    )
    risk_cards = []
    for f in risks:
        d = f.model_dump()
        d["disputed"] = f.status == "disputed"
        # 生产工作流：高危与缺少直接法条依据的结论必须由法务确认。
        d["reviewStatus"] = (
            "pending_review" if f.severity == "high" or f.legalBasis.tier == "none" else "not_required"
        )
        risk_cards.append(d)

    # Deterministic playbook conclusions are independent of the model reviewer.
    for finding in state.get("playbook_findings", []):
        card = finding.model_dump() if hasattr(finding, "model_dump") else deepcopy(finding)
        card.setdefault("source", "playbook")
        card.setdefault("sourceDetails", {key: card[key] for key in ("playbookId", "version", "contentHash", "ruleId") if key in card})
        card.setdefault("status", "upheld")
        card.setdefault("disputed", False)
        existing = next((r for r in risk_cards if (r.get("clauseId"), r.get("riskType"), r.get("severity")) == (card.get("clauseId"), card.get("riskType"), card.get("severity"))), None)
        if existing is None:
            risk_cards.append(card)
        else:
            if not existing.get("sources"):
                existing["sources"] = [{"source": existing.get("source", "model"), **existing.get("sourceDetails", {})}]
            existing["sources"].append({"source": "playbook", **card["sourceDetails"], **{key: card.get(key) for key in ("evidence", "confidence", "clauseQuote", "location", "suggestionClauseText", "escalationRecommendation")}})
            existing["requiresHumanReview"] = existing.get("requiresHumanReview", False) or card.get("requiresHumanReview", False)
            existing["escalationRecommendation"] = existing.get("escalationRecommendation") or card.get("escalationRecommendation", card.get("escalationPolicy", ""))
    risk_cards.sort(key=lambda r: (_SEVERITY_ORDER.get(r.get("severity"), 9), r.get("clauseId", "")))
    counts = {sev: sum(r.get("severity") == sev for r in risk_cards) for sev in ("high", "medium", "low")}
    by_category = {}
    for card in risk_cards:
        worker = card.get("worker", "playbook")
        by_category[worker] = by_category.get(worker, 0) + 1

    # 条款导航（含风险级别红/黄/绿）
    # S8：同条款多个 findings 时取最高严重度（后写覆盖会丢更严重档）
    risk_clause_ids: dict[str, str] = {}
    for card in risk_cards:
        clause_id, severity = card.get("clauseId"), card.get("severity")
        cur = risk_clause_ids.get(clause_id)
        if cur is None or _SEVERITY_ORDER.get(severity, 9) < _SEVERITY_ORDER.get(cur, 9):
            risk_clause_ids[clause_id] = severity
    clause_nav = []
    for c in clauses:
        sev = risk_clause_ids.get(c.clauseId)
        clause_nav.append(
            {
                "clauseId": c.clauseId,
                "quote": c.quote[:300],
                "location": c.location,
                "riskLevel": sev if sev else None,
            }
        )

    meta = {
        "reviewMode": mode,
        "mock": using_mock(),
        "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
        "latencyMs": int((time.time() - start) * 1000),
        "legalCorpus": LegalCorpusSnapshot.from_manual().model_dump(),
    }
    playbook_snapshot = state.get("playbook_snapshot")
    if playbook_snapshot is not None:
        meta["playbookSnapshot"] = deepcopy(playbook_snapshot)
        # 旧运行可能只有绑定快照，没有规则执行记录；保留兼容标记。
        meta["playbookApplication"] = "snapshot_only"
        if state.get("playbook_execution"):
            meta["playbookApplication"] = state["playbook_execution"].get("application", "executed")
            meta["playbookExecution"] = deepcopy(state["playbook_execution"])

    report = ReportOut(
        contract={
            "name": state.get("contract_name", ""),
            "type": state.get("contract_type", ""),
            "clauseCount": len(clauses),
        },
        summary={
            "high": counts["high"],
            "medium": counts["medium"],
            "low": counts["low"],
            "total": len(risk_cards),
            "byCategory": by_category,
        },
        risks=risk_cards,
        clauses=clause_nav,
        meta=meta,
    )
    return {"report": normalize_report(report.model_dump())}
