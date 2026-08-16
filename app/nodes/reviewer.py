"""复核 agent（SPEC 2.3 ③）：对抗性复核——过滤误报 + 法条查证 + 打回重证（一次迭代）。

- 职责分工：判断风险是否真实成立（三档风险全查）；核对法条引用只对"直接依据"档。
- 查证漏洞防堵：高危风险的「无直接依据」档也必须确认"确无直接法条"（verify 模式）。
- B 档（复核直滤）：驳回直接 rejected（不入报告）。
- C 档（复核+打回）：驳回带理由打回原 worker 重证——worker 认可则撤回
  （status=rejected，reason=worker 自撤），有证据则坚持（status=disputed，进报告标「有争议」）。
- 只迭代一次，不无限循环；复核节点在 LangGraph 里可插拔（A 档跳过）。
- mock/规则模式：规则命中即成立（upheld），链路自检用。
"""
from __future__ import annotations

import json

from pydantic import ValidationError

from ..config import SCHEMA_MAX_RETRIES, using_mock
from ..legal.manual import load_manual
from ..llm import LLMClient, LLMError
from ..state import ContractState, Finding, ReviewResult

REVIEW_SYSTEM = (
    "你是对抗性合同风险复核专家。你的职责：\n"
    "1. 判断每个候选风险是否真实成立（三档风险全查，过滤误报）；\n"
    "2. 核对法条引用：只对 direct 档核对条文号与原文是否准确；\n"
    "3. 高危且法条为 none 档的，必须确认\"确无直接法条\"（防 worker 用标无依据逃避查证）。\n"
    "输入：候选发现 + 相关条款事实 + 其引用的法条原文。\n"
    "输出严格 JSON：{\"verdicts\": [{\"findingId\": \"...\", \"verdict\": \"upheld|rejected\", "
    "\"reason\": \"驳回的具体理由（判定不成立时必填，供打回重证）\", "
    "\"verifyNote\": \"查证记录（法条核对结果 / 高危无依据确认）\"}]}\n"
    "规则：\n"
    "- 判定不成立必须给具体可反驳的理由，禁止笼统否定；\n"
    "- 严格标准（与 worker 一致）：条款已满足合理商业约定（验收期 ≥ 5 个工作日、"
    "IP 归属已明确、有保密/管辖/通知条款）时，对应发现视为误报驳回；"
    "\"可以写得更完善\"式发现驳回；\n"
    "- 不确定时倾向 upheld（宁可标争议）。"
)

REVERIFY_SYSTEM = (
    "你是原风险识别 worker。复核 agent 对你的发现提出了驳回理由，请重证：\n"
    "1. 若驳回理由成立、你确实误报 → 撤回（withdraw）；\n"
    "2. 若你有条款事实/关键数值支撑 → 坚持（uphold）并补充论证。\n"
    "输出严格 JSON：{\"decision\": \"withdraw|uphold\", \"justification\": \"重证理由\"}。"
)


def _mk_result(f: Finding, verdict: str, **kw) -> ReviewResult:
    """构造 ReviewResult 并带 finding 三元组快照（供复核模块级口径判定真误报/误杀）。"""
    return ReviewResult(
        findingId=f.id, verdict=verdict, clauseId=f.clauseId,
        riskType=f.riskType, severity=f.severity, **kw,
    )


def _reverify(
    finding: Finding,
    reject_reason: str,
    llm: LLMClient | None,
    clauses: list,
) -> tuple[str, str]:
    """打回重证：返回 (status, justification)。只迭代一次。"""
    if using_mock():
        # 规则降级：确定性可复现——按 finding id 奇偶覆盖两条路径（撤回/坚持）
        if int("".join(ch for ch in finding.id if ch.isdigit()) or "0") % 2 == 0:
            return "rejected", "mock 重证：认可驳回理由，撤回误报"
        return "disputed", "mock 重证：有条款事实支撑，坚持原判断"
    if llm is None:
        return "rejected", "无 LLM，默认撤回"
    user = (
        f"你的发现：\n条款 {finding.clauseId}：{finding.clauseQuote[:200]}\n"
        f"风险：{finding.riskType}/{finding.severity}\n证据：{finding.evidence[:200]}\n"
        f"法条：{finding.legalBasis.tier} {finding.legalBasis.articleId} {finding.legalBasis.quote[:150]}\n"
        f"复核驳回理由：{reject_reason}\n请重证并输出 JSON。"
    )
    for attempt in range(SCHEMA_MAX_RETRIES + 1):
        try:
            data = llm.chat_json(
                [
                    {"role": "system", "content": REVERIFY_SYSTEM},
                    {"role": "user", "content": user},
                ]
            )
            decision = data.get("decision", "withdraw")
            justification = data.get("justification", "")
            if decision == "uphold":
                return "disputed", justification
            return "rejected", justification
        except (LLMError, json.JSONDecodeError):
            if attempt < SCHEMA_MAX_RETRIES:
                continue
            break
    return "rejected", "重证调用失败，默认撤回"


def build_reviewer_node(retriever, llm: LLMClient | None = None, mode: str = "B", verify: bool = True):
    """构造复核节点。mode: B 直滤 / C 打回重证；verify: 查证模式开关。"""

    def node(state: ContractState) -> dict:
        findings: list[Finding] = state.get("findings", [])
        clauses = state.get("clauses", [])
        proposed = [f for f in findings if f.status == "proposed"]
        results: list[ReviewResult] = list(state.get("review_results", []))
        updated = list(findings)
        if not proposed:
            return {"review_results": results}

        # ---- mock：规则命中即成立（upheld）----
        if using_mock():
            for f in proposed:
                f.status = "upheld"
                results.append(_mk_result(f, "upheld", reason="", verifyNote="mock"))
            return {"findings": updated, "review_results": results}

        if llm is None:
            raise RuntimeError("reviewer_node requires LLMClient (non-mock)")

        # ---- 组装复核输入：发现 + 条款事实 + 引用的法条原文 + 检索相关法条 ----
        manual = {a.id: a for a in load_manual()}
        lines = []
        for f in proposed:
            lines.append(
                f"[{f.id}] 条款 {f.clauseId}：{f.clauseQuote[:200]}\n"
                f"风险：{f.riskType}/{f.severity}\n"
                f"法条：{f.legalBasis.tier} {f.legalBasis.articleId} {f.legalBasis.version} {f.legalBasis.quote[:150]}\n"
                f"证据：{f.evidence[:200]}"
            )
        # 查证材料：direct 引用条文原文 + 检索相关法条
        verify_blocks = []
        for f in proposed:
            if f.legalBasis.tier == "direct" and f.legalBasis.articleId in manual:
                a = manual[f.legalBasis.articleId]
                verify_blocks.append(f"[核对] {a.id}|{a.version}|{a.article} {a.text[:300]}")
        if verify:
            for f in proposed:
                if f.legalBasis.tier == "none" and f.severity == "high":
                    hits = retriever.search(f"{f.riskType} {f.clauseQuote[:80]}", top_k=3)
                    for a in hits:
                        verify_blocks.append(f"[检索] {a.id}|{a.version}|{a.article} {a.text[:300]}")
        user = (
            "候选风险发现：\n" + "\n".join(lines)
            + "\n\n相关条款事实：\n"
            + "\n".join(f"[{c.clauseId}] {c.quote[:200]}" for c in clauses[:30])
            + ("\n\n查证材料：\n" + "\n".join(verify_blocks) if verify_blocks else "")
            + "\n请输出复核裁决 JSON。"
        )
        verdicts: list[dict] = []
        for attempt in range(SCHEMA_MAX_RETRIES + 1):
            try:
                data = llm.chat_json(
                    [
                        {"role": "system", "content": REVIEW_SYSTEM},
                        {"role": "user", "content": user},
                    ]
                )
                raw = data.get("verdicts", []) if isinstance(data, dict) else []
                verdicts = [v for v in raw if isinstance(v, dict) and v.get("findingId")]
                break
            except (LLMError, json.JSONDecodeError):
                if attempt < SCHEMA_MAX_RETRIES:
                    continue
                verdicts = []  # 复核失败：全部放行（部分成功原则）
        verdict_map = {v["findingId"]: v for v in verdicts}

        # ---- 应用裁决 ----
        for f in proposed:
            v = verdict_map.get(f.id)
            if v is None or v.get("verdict") == "upheld":
                f.status = "upheld"
                results.append(_mk_result(f, "upheld", reason="", verifyNote=v.get("verifyNote", "") if v else ""))
                continue
            reason = v.get("reason", "复核判定不成立")
            verify_note = v.get("verifyNote", "")
            if mode == "B":
                f.status = "rejected"
                f.rejectReason = reason
                results.append(_mk_result(f, "rejected", reason=reason, wasRejectedByReviewer=True, verifyNote=verify_note))
            else:  # C：打回重证（一次迭代）
                final_status, justification = _reverify(f, reason, llm, clauses)
                if final_status == "rejected":
                    f.status = "rejected"
                    f.rejectReason = f"复核驳回：{reason}；worker 自撤：{justification}"
                    results.append(_mk_result(f, "rejected", reason=reason, wasRejectedByReviewer=True, revertedAfterReverify=True, verifyNote=verify_note))
                else:
                    f.status = "disputed"
                    f.reVerifyJustification = justification
                    results.append(_mk_result(f, "disputed", reason=reason, wasRejectedByReviewer=True, upheldAfterReverify=True, verifyNote=verify_note))
        return {"findings": updated, "review_results": results}

    return node
