"""规则基线（SPEC 2.6：免费第二大脑）——确定性代码规则，零共偏、零成本。

作为"打赢基线"的对比对象：与系统同 schema 输出报告，score.py 用同一计分口径。
保持朴素：只抓最简单直接的确定性模式（rule_checker.py 单一来源），
否则基线本身就是答案，worker 永远赢不了。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.legal.rule_checker import rule_baseline_findings  # noqa: E402
from app.nodes.extract import split_into_chunks, _rule_extract  # noqa: E402


def baseline_report(contract_text: str, contract_name: str = "", contract_type: str = "") -> dict:
    """规则基线报告（与系统报告同 schema，仅 risks/clauses 语义相同）。"""
    facts = []
    for chunk in split_into_chunks(contract_text):
        facts.extend(_rule_extract(chunk))
    findings = rule_baseline_findings(facts)
    counts = {"high": 0, "medium": 0, "low": 0}
    for f in findings:
        counts[f.severity] += 1
    risks = []
    for f in findings:
        d = f.model_dump()
        d["disputed"] = False
        risks.append(d)
    return {
        "contract": {"name": contract_name, "type": contract_type, "clauseCount": len(facts)},
        "summary": {**counts, "total": len(risks), "byCategory": {}},
        "risks": risks,
        "clauses": [
            {"clauseId": c.clauseId, "quote": c.quote[:300], "riskLevel": None} for c in facts
        ],
        "meta": {"reviewMode": "baseline", "mock": True, "engine": "rule_baseline"},
    }
