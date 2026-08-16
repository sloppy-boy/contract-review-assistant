"""报告生成节点（SPEC 2.3 ④）：分级汇总 + 按严重度排序 + 统一 JSON schema。

- 有复核配置（B/C）时只读 status ∈ {upheld, disputed}；
  无复核配置（消融 A 档）时所有 proposed 直接视为通过进报告（否则 A 档报告为空，消融无法对比）。
- 报告 schema（ReportOut）一份结构三处消费：评测脚本 / 前端 / Word 导出。
- clauses 全量输出（前端条款导航 + 干净组误报密度分母）。
"""
from __future__ import annotations

import time

from ..config import REVIEW_MODE, using_mock
from ..state import ContractState, ReportOut

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
        risk_cards.append(d)

    # 条款导航（含风险级别红/黄/绿）
    risk_clause_ids = {f.clauseId: f.severity for f in effective}
    clause_nav = []
    for c in clauses:
        sev = risk_clause_ids.get(c.clauseId)
        clause_nav.append(
            {
                "clauseId": c.clauseId,
                "quote": c.quote[:300],
                "riskLevel": sev if sev else None,
            }
        )

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
        meta={
            "reviewMode": mode,
            "mock": using_mock(),
            "generatedAt": time.strftime("%Y-%m-%d %H:%M:%S"),
            "latencyMs": int((time.time() - start) * 1000),
        },
    )
    return {"report": report.model_dump()}
