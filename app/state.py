"""合同事实黑板（共享记忆）schema + LangGraph state（SPEC 2.3）。

实现约束：用 LangGraph state 实现，禁止外部存储/数据库/缓存系统。
findings 通道带自定义 reducer（见 findings_reducer）：
- worker 并行追加时：同条款 + 同风险类型 + 同严重度 的重复发现合并
  （保留 evidence 更充分者，配置无关）——SPEC 2.3 去重规则第一层；
  跨 worker 撞车在 A 档（无复核）由此退化为合并，B/C 档由复核按 id 裁决。
- 复核/重证更新时：按 finding id 覆盖（状态机 proposed→upheld/rejected/disputed）。
"""
from __future__ import annotations

from operator import add
from typing import Annotated, Literal, TypedDict

from pydantic import BaseModel, Field

Severity = Literal["high", "medium", "low"]
Tier = Literal["direct", "indirect", "none"]
Status = Literal["proposed", "upheld", "rejected", "disputed"]


class LegalBasis(BaseModel):
    """法条依据三档（SPEC 2.4）。direct 必须有 articleId/version/quote。"""
    tier: Tier = "none"
    articleId: str = ""
    version: str = ""
    quote: str = ""


class ClauseFact(BaseModel):
    """条款事实卡片（黑板 clauseFacts 单条）。"""
    clauseId: str = Field(description="条款编号，如 12.3")
    quote: str = Field(description="条款原文（摘要级，保留关键数值）")
    location: str = Field(default="", description="原文位置（页码/段号），供报告溯源")
    keyNumbers: dict[str, float | str] = Field(
        default_factory=dict,
        description="关键数值：amount/paymentDays/penaltyRatio/defectDays/warrantyMonths…",
    )
    definitions: dict[str, str] = Field(
        default_factory=dict, description="定义条款映射：术语 → 定义所在 clauseId"
    )
    references: list[str] = Field(default_factory=list, description="交叉引用：本条引用的其他 clauseId")


class Finding(BaseModel):
    """风险发现（黑板 findings 单条）。worker 输出即 status=proposed。"""
    id: str = Field(description="全局唯一 id，如 w-payment_invoice-12.3-0")
    worker: str = Field(description="产出 worker 类目 id")
    clauseId: str
    clauseQuote: str
    riskType: str = Field(description="风险类型（子类型枚举，见风险矩阵）")
    severity: Severity
    legalBasis: LegalBasis = Field(default_factory=LegalBasis)
    evidence: str = Field(description="引用的 clauseFacts 依据（哪条事实/关键数值支撑判断）")
    suggestion: str = Field(description="修改建议")
    suggestionClauseText: str = Field(default="", description="可直接替换的示范条款")
    status: Status = "proposed"
    rejectReason: str = Field(default="", description="复核驳回理由（status=rejected 时）")
    reVerifyJustification: str = Field(default="", description="重证后坚持的理由（status=disputed 时）")

    def dedup_key(self) -> tuple[str, str, str]:
        return (self.clauseId, self.riskType, self.severity)


def findings_reducer(current: list[Finding], update: list[Finding]) -> list[Finding]:
    """findings 通道 reducer：
    1) 已存在 id → 覆盖（复核状态流转 / 打回重证更新 justification）
    2) 同条款+同风险类型+同严重度的 proposed 重复 → 合并（保留 evidence 更充分者）
    3) 否则追加
    """
    merged: list[Finding] = list(current)
    for f in update:
        idx = next((i for i, x in enumerate(merged) if x.id == f.id), None)
        if idx is not None:
            merged[idx] = f
            continue
        if f.status == "proposed":
            j = next(
                (
                    i
                    for i, x in enumerate(merged)
                    if x.status == "proposed" and x.dedup_key() == f.dedup_key()
                ),
                None,
            )
            if j is not None:
                if len(f.evidence) > len(merged[j].evidence):
                    merged[j] = f
                continue
        merged.append(f)
    return merged


class ReviewResult(BaseModel):
    """复核模块级指标所需的过程记录（供 score.py 复核模块级口径，SPEC 2.6）。"""
    findingId: str
    verdict: Status                       # upheld / rejected / disputed
    clauseId: str = ""                    # 三元组快照（供评分判定真误报/误杀）
    riskType: str = ""
    severity: str = ""
    reason: str = ""
    wasRejectedByReviewer: bool = False   # 复核驳回（B/C）
    revertedAfterReverify: bool = False   # 打回重证后 worker 自撤
    upheldAfterReverify: bool = False     # 打回重证后 worker 坚持（disputed）
    verifyNote: str = ""                  # 查证记录（法条核对/高危 none 确认）


class ContractState(TypedDict, total=False):
    # 输入
    contract_text: str
    contract_type: str                     # purchase | sale
    contract_name: str
    # 黑板
    clauses: list[ClauseFact]
    findings: Annotated[list[Finding], findings_reducer]
    review_results: list[ReviewResult]
    # 报告
    report: dict
    # 过程与诊断
    errors: list[str]
    trace: list[dict]
    meta: dict                             # 耗时 / token / 配置快照


class RiskItemOut(BaseModel):
    """worker 输出强制 schema（SPEC 2.3）。status 由复核更新，不在此 schema 内。"""
    clauseId: str
    clauseQuote: str
    riskType: str
    severity: Severity
    legalBasis: LegalBasis = Field(default_factory=LegalBasis)
    evidence: str
    suggestion: str
    suggestionClauseText: str = ""


class ReportOut(BaseModel):
    """报告统一 JSON schema（一份结构三处消费：评测/前端/Word 导出）。"""
    contract: dict = Field(default_factory=dict)
    summary: dict = Field(default_factory=dict)       # 分级汇总 + 分布
    risks: list[dict] = Field(default_factory=list)   # 按严重度排序的风险卡片
    clauses: list[dict] = Field(default_factory=list) # 全条款导航（干净组误报密度分母）
    meta: dict = Field(default_factory=dict)
