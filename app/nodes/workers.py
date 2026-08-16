"""风险识别 workers（SPEC 2.3 ②）：每类一个节点，并行扇出。

- 每个 worker 只收到：职责相关条款 + 被这些条款引用的其他条款摘要（2~3 句）——
  防"看单条下结论"（交叉引用盲区的解法）。
- 输入硬预算 ≤ 8K tokens：法条只取 top-3、引用摘要每条约 ≤3 句、超出裁剪而非全塞。
- 输出强制结构化 schema（RiskItemOut），status=proposed；schema 校验失败重试 1 次，
  仍失败跳过该条（不崩链）。
- worker 分类与风险矩阵一一对应（矩阵为分类唯一来源）。
- mock/规则模式（无 key）：返回规则基线中归属本类目的朴素发现（确定性，链路自检）。
"""
from __future__ import annotations

import json

from pydantic import ValidationError

from ..config import (
    REF_SUMMARY_MAX_SENTENCES,
    SCHEMA_MAX_RETRIES,
    TOP_K_ARTICLES,
    WORKER_INPUT_BUDGET_TOKENS,
    using_mock,
)
from ..legal.risk_matrix import RISK_MATRIX, SUBTYPE_TO_CATEGORY, WORKER_CATEGORIES
from ..legal.rule_checker import rule_baseline_findings
from ..llm import LLMClient, LLMError
from ..state import ClauseFact, ContractState, Finding, RiskItemOut

# 每类 worker 的条款触发关键词（与风险矩阵 checklist 对应；覆盖缺失型/免责型表述）
WORKER_TRIGGERS: dict[str, list[str]] = {
    "payment_invoice": ["付款", "支付", "货款", "预付款", "价款", "结算", "发票", "开票", "定金", "逾期", "款项"],
    "acceptance_delivery": ["验收", "交付", "检验", "收货", "交货", "安装", "调试", "签收"],
    "ip": ["知识产权", "专利", "商标", "著作权", "软件", "技术成果", "源代码", "版权"],
    "data_compliance": ["个人信息", "数据", "隐私", "用户信息", "信息处理", "客户信息"],
    "breach_liability": ["违约金", "定金", "赔偿", "违约责任", "损失", "责任上限", "滞纳金", "罚则", "无需承担"],
    "termination": ["解除", "终止", "撤销", "解除权"],
    "confidentiality": ["保密", "商业秘密", "机密", "保密义务"],
    "non_compete": ["竞业", "排他", "不竞争", "不得从事", "不得与", "任何客户", "限制交易", "禁止"],
    "jurisdiction": ["管辖", "仲裁", "诉讼", "法院", "争议解决", "起诉"],
    "notice": ["通知", "送达", "联系地址", "告知"],
    "force_majeure": ["不可抗力", "免责", "人力不可抗", "市场波动", "原材料价格"],
    "tax": ["税", "含税", "发票", "税率", "税费"],
    "subcontract": ["分包", "转包", "外包", "第三方履行", "再委托", "第三方"],
}

# 每类 worker 的附加特化指令（收紧误报，与风险矩阵 rubric 对齐）
WORKER_EXTRA: dict[str, str] = {
    "acceptance_delivery": (
        "验收特化规则：\n"
        "- '符合国家标准/行业标准 + 双方确认的技术规格书/图纸/样品'视为验收标准已明确，不得报\"验收标准缺失或模糊\"；"
        "仅当没有任何可执行标准时才报。\n"
        "- 验收不通过后的处理流程缺失：若合同另有违约金/违约救济兜底条款（如第五条违约责任），"
        "不单独构成\"验收不通过的后果不明\"风险。\n"
        "- 验收期限：≥ 5 个工作日不报\"验收期限过短\"。"
    ),
    "payment_invoice": (
        "付款特化规则：\n"
        "- 即使合同另有违约金条款，若存在明确的免责/豁免表述（如'无需承担任何逾期责任'、"
        "'不承担任何责任'），仍属风险（免责表述使违约金条款落空），必报。\n"
        "- 预付款比例 > 80% 且无担保/退款机制 → high；付款期限 > 90 天且无逾期付款违约责任 → medium。"
    ),
    "data_compliance": (
        "数据合规特化规则：任何一方要求对方提供其员工/终端用户的个人信息，且合同中无授权依据/"
        "合法基础说明（如'为履行本合同所必需'），必报（个人信息保护法第 6/13 条）。"
    ),
    "non_compete": (
        "竞业限制特化规则：'不得与甲方客户交易'、'不得从事同类业务'等排他/竞业表述，"
        "若无期限/地域/范围边界，报'竞业限制范围过宽'；'任何客户'+'三年'及以上+无地域限制 → high。"
    ),
    "confidentiality": (
        "保密特化规则：条款内容为'（此处空白）'或完全缺失时，若合同涉及商业秘密/合作信息/技术资料，"
        "报'保密条款缺失'（medium）。"
    ),
    "subcontract": (
        "分包特化规则：'全部义务分包'或'分包无需甲方同意'式条款 → '分包未经同意'（medium）。"
    ),
    "jurisdiction": (
        "管辖特化规则：约定向与争议无实际联系的地点（如西藏拉萨市）法院起诉 → "
        "'管辖约定不明确或无效'（high，民诉法第 35 条要求与争议有实际联系）。"
    ),
    "breach_liability": (
        "违约特化规则：\n"
        "- 违约金比例 < 10% 且无'赔偿损失'兜底表述 → '违约金过低'（medium）；\n"
        "- 同时存在'无需承担任何责任'类免责表述时，违约金条款落空，相关违约责任风险必报。"
    ),
}

WORKER_SYSTEM = (
    "你是合同风险识别专家，负责「{name}」类风险的审查。\n"
    "你的职责范围（子类型）：{subtypes}。\n"
    "严重度校准依据（rubric）：\n{rubric}\n"
    "判定 checklist：\n{checklist}\n"
    "输入：与你职责相关的合同条款（含被引用条款摘要）与检索到的法条依据（top-3）。\n"
    "输出严格 JSON：{{\"risks\": [{{\"clauseId\": \"...\", \"clauseQuote\": \"原文摘录\", "
    "\"riskType\": \"子类型\", \"severity\": \"high|medium|low\", "
    "\"legalBasis\": {{\"tier\": \"direct|indirect|none\", \"articleId\": \"\", \"version\": \"\", \"quote\": \"\"}}, "
    "\"evidence\": \"引用的条款事实/关键数值\", \"suggestion\": \"修改建议\", "
    "\"suggestionClauseText\": \"可直接替换的示范条款\"}}]}}\n"
    "规则：\n"
    "1. 只输出本类目下的风险（riskType 必须取自上面子类型列表）。\n"
    "2. severity 必须按 rubric 客观阈值校准。\n"
    "3. 法条三档：direct=手册命中具体条文（引原文+articleId+version）；"
    "indirect=只有原则性条款（标\"间接依据：诚信/公平原则\"，注明非直接条文）；"
    "none=必须显式写\"提示性质，无明确法条依据\"，禁止硬编。\n"
    "4. evidence 必须引用具体条款事实或关键数值支撑判断。\n"
    "5. 无风险时输出空数组。\n"
    "6. 严格标准（防过度审查）：只报存在实际法律风险的条款。下列情况**不报**：\n"
    "   a) 条款已满足合理商业约定：验收期 ≥ 5 个工作日、IP 归属已明确、有保密条款、有管辖条款；\n"
    "   b) \"可以写得更完善\"式的建议（如\"建议补充 X 条款\"但缺失不构成实际风险）；\n"
    "   c) 风险矩阵 rubric 未达阈值的情形——rubric 是成立性与严重度的硬约束，条件不满足不得输出。\n"
    "7. 定制 vs 标准货物：仅当合同涉及定制/委托开发成果且归属未约定时，才报知识产权归属风险；"
    "标准货物买卖的 IP 条款（归属各自权利人 + 不侵权担保）不报。\n"
    "8. 缺失型风险（如保密条款缺失、验收不通过处理缺失）：仅当其缺失将导致一方核心权利无保障时才报，"
    "且严重度按 rubric 定；标准模板已含保密/管辖/通知条款时不得报对应缺失。"
)


def _query_for(category: str) -> str:
    cat = RISK_MATRIX[category]
    return f"{cat.name} 合同条款风险 {' '.join(cat.sub_types)}"


def _clip_context(blocks: list[str], budget_chars: int) -> str:
    """输入硬预算：按字符数估算裁剪（中文 1 token ≈ 1.5~2 字符，取保守 1.5）。"""
    out: list[str] = []
    used = 0
    for b in blocks:
        cost = len(b) + 2
        if used + cost > budget_chars:
            break
        out.append(b)
        used += cost
    return "\n".join(out)


def _ref_summaries(clause: ClauseFact, all_clauses: list[ClauseFact]) -> list[str]:
    """被引用条款摘要：每条 ≤ REF_SUMMARY_MAX_SENTENCES 句。"""
    out: list[str] = []
    for ref in clause.references[:5]:
        target = next((x for x in all_clauses if x.clauseId == ref), None)
        if target is None or target.clauseId == clause.clauseId:
            continue
        sents = [s.strip() for s in target.quote.replace("\n", "。").split("。") if s.strip()]
        summary = "。".join(sents[:REF_SUMMARY_MAX_SENTENCES])
        out.append(f"[{target.clauseId}] {summary}")
    return out


def build_worker_node(category: str, retriever, llm: LLMClient | None = None):
    """构造单个 worker 节点（闭包注入 retriever/llm）。"""
    triggers = WORKER_TRIGGERS[category]
    cat = RISK_MATRIX[category]

    def node(state: ContractState) -> dict:
        clauses: list[ClauseFact] = state.get("clauses", [])
        # mock/规则模式：返回规则基线中归属本类目的朴素发现（数字=基线，明确标注 mock）
        if using_mock():
            out: list[Finding] = []
            for f in rule_baseline_findings(clauses):
                if SUBTYPE_TO_CATEGORY.get(f.riskType) == category:
                    out.append(f)
            return {"findings": out}

        relevant = [c for c in clauses if any(t in c.quote for t in triggers)]
        if not relevant:
            return {"findings": []}

        # 引用摘要（交叉引用盲区解法）
        ref_blocks: list[str] = []
        for c in relevant:
            ref_blocks.extend(_ref_summaries(c, clauses))

        # 法条检索 top-3（只给原文+ID+版本）
        articles = retriever.search(_query_for(category), top_k=TOP_K_ARTICLES)
        article_blocks = [
            f"[{a.article.id}|{a.article.version}|{a.article.article}] {a.article.text[:300]}"
            for a in articles
        ]

        # 组装 + 预算裁剪（超出裁剪而非全塞）
        budget_chars = int(WORKER_INPUT_BUDGET_TOKENS * 1.5)
        clause_blocks = [f"[{c.clauseId}] {c.quote[:300]}" for c in relevant]
        body = _clip_context(
            ["相关条款：\n" + "\n".join(clause_blocks)]
            + (["被引用条款摘要：\n" + "\n".join(ref_blocks)] if ref_blocks else [])
            + (["法条依据：\n" + "\n".join(article_blocks)] if article_blocks else []),
            budget_chars,
        )
        user = body + "\n请输出风险识别 JSON。"

        sys = WORKER_SYSTEM.format(
            name=cat.name,
            subtypes="、".join(cat.sub_types),
            rubric="\n".join(f"- {r.condition} → {r.severity}" for r in cat.rubric),
            checklist="\n".join(f"- {c}" for c in cat.checklist),
        )
        extra = WORKER_EXTRA.get(category)
        if extra:
            sys = sys + "\n" + extra
        items: list[RiskItemOut] = []
        for attempt in range(SCHEMA_MAX_RETRIES + 1):
            try:
                data = llm.chat_json(
                    [
                        {"role": "system", "content": sys},
                        {"role": "user", "content": user},
                    ]
                )
                raw = data.get("risks", []) if isinstance(data, dict) else []
                items = []
                for it in raw:
                    try:
                        items.append(RiskItemOut(**it))
                    except ValidationError:
                        continue  # 单条坏数据跳过，不崩链
                break
            except (LLMError, json.JSONDecodeError):
                if attempt < SCHEMA_MAX_RETRIES:
                    continue
                items = []  # 该 worker 本次无输出（部分成功原则）
        findings: list[Finding] = []
        for i, it in enumerate(items):
            findings.append(
                Finding(
                    id=f"w_{category}-{it.clauseId}-{i}",
                    worker=category,
                    clauseId=it.clauseId,
                    clauseQuote=it.clauseQuote,
                    riskType=it.riskType,
                    severity=it.severity,
                    legalBasis=it.legalBasis,
                    evidence=it.evidence,
                    suggestion=it.suggestion,
                    suggestionClauseText=it.suggestionClauseText,
                    status="proposed",
                )
            )
        return {"findings": findings}

    return node


def worker_fanout_categories() -> list[str]:
    """与风险矩阵一一对应的 worker 分类（唯一来源）。"""
    return list(WORKER_CATEGORIES)
