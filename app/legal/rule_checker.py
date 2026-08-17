"""确定性朴素规则基线（SPEC 2.6：规则基线保持朴素，只抓最简单直接的确定性模式）。

- 这是"规则基线"（对比对象）与 mock 降级模式的唯一实现：零共偏、零成本。
- 三源解耦：植入缺陷含规则抓不到的变体（组合风险/跨条款推理/表述含糊），
  基线保持朴素，worker（LLM + 风险矩阵 checklist）判定独立于植入记录。
- 输入 clauseFacts（黑板条款事实），输出朴素 finding 列表（复用 Finding schema）。
"""
from __future__ import annotations

from ..patterns import DAYS_RE, PCT_RE
from ..state import ClauseFact, Finding, LegalBasis

# 朴素规则（刻意简单；更复杂判定留给 worker）
# "赔偿"刻意不含在内：保密/瑕疵担保条款的"赔偿"字样与付款逾期责任无关，否则规则永不触发
_NO_PENALTY_WORDS = ("违约金", "逾期", "滞纳金")
_NO_ARBITRATION_ORG = ("仲裁委", "仲裁委员会", "仲裁机构")


def rule_baseline_findings(clauses: list[ClauseFact]) -> list[Finding]:
    """对 clauseFacts 应用朴素规则，返回 proposed finding 列表（rule_baseline / mock 共用）。"""
    out: list[Finding] = []
    all_text = "\n".join(c.quote for c in clauses)

    for c in clauses:
        text = c.quote
        # --- 规则 1：违约金比例 > 30% → high（民法典 585 可调减）
        if "违约金" in text:
            for m in PCT_RE.finditer(text):
                ratio = float(m.group(1))
                if ratio > 30:
                    out.append(
                        _mk(
                            c, "w_rule", "违约金过高",
                            "high",
                            LegalBasis(
                                tier="direct", articleId="CIVIL-585",
                                version="中华人民共和国民法典（2021-01-01 施行）",
                                quote="约定的违约金过分高于造成的损失的，人民法院或者仲裁机构可以根据当事人的请求予以适当减少。",
                            ),
                            f"违约金比例 {ratio}% 超过 30%，明显过高",
                        )
                    )
                    break
        # --- 规则 2：定金比例 > 20% → medium（民法典 586）
        if "定金" in text:
            for m in PCT_RE.finditer(text):
                ratio = float(m.group(1))
                if ratio > 20:
                    out.append(
                        _mk(
                            c, "w_rule", "定金条款违规",
                            "medium",
                            LegalBasis(
                                tier="direct", articleId="CIVIL-586",
                                version="中华人民共和国民法典（2021-01-01 施行）",
                                quote="定金的数额由当事人约定；但是，不得超过主合同标的额的百分之二十，超过部分不产生定金的效力。",
                            ),
                            f"定金比例 {ratio}% 超过主合同标的额 20%",
                        )
                    )
                    break
        # --- 规则 3：付款期限 > 90 天且全合同无逾期违约金/赔偿字样 → medium
        if "付" in text or "款" in text:
            days = None
            for m in DAYS_RE.finditer(text):
                days = int(m.group(1))
            if days is not None and days > 90 and not any(w in all_text for w in _NO_PENALTY_WORDS):
                out.append(
                    _mk(
                        c, "w_rule", "付款期限过长",
                        "medium",
                        LegalBasis(
                            tier="indirect",
                            articleId="CIVIL-509",
                            version="中华人民共和国民法典（2021-01-01 施行）",
                            quote="当事人应当按照约定全面履行自己的义务。当事人应当遵循诚信原则，根据合同的性质、目的和交易习惯履行通知、协助、保密等义务。",
                        ),
                        f"付款期限 {days} 天超过 90 天，且全合同未见逾期付款违约金/赔偿条款",
                    )
                )
        # --- 规则 4：约定仲裁但未约定仲裁机构/仲裁地 → medium（仲裁协议无效风险）
        if "仲裁" in text and not any(w in text for w in _NO_ARBITRATION_ORG):
            out.append(
                _mk(
                    c, "w_rule", "只约定仲裁未约定仲裁机构/地点",
                    "medium",
                    LegalBasis(
                        tier="indirect",
                        articleId="CIVPROC-35",
                        version="中华人民共和国民事诉讼法（2023 修正，2024-01-01 施行）",
                        quote="合同或者其他财产权益纠纷的当事人可以书面协议选择…与争议有实际联系的地点的人民法院管辖…",
                    ),
                    "仅约定仲裁而未指定仲裁机构/仲裁地，仲裁协议存在无效风险",
                )
            )
    return out


def _mk(
    clause: ClauseFact,
    worker: str,
    risk_type: str,
    severity: str,
    basis: LegalBasis,
    evidence: str,
) -> Finding:
    return Finding(
        id=f"w_{worker}-{clause.clauseId}-{risk_type}",
        worker=worker,
        clauseId=clause.clauseId,
        clauseQuote=clause.quote[:300],
        riskType=risk_type,
        severity=severity,
        legalBasis=basis,
        evidence=evidence,
        suggestion="参照法定标准调整约定比例/期限，并补足对应违约责任条款。",
        suggestionClauseText="",
    )
