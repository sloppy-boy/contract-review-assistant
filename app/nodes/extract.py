"""条款抽取节点（SPEC 2.3 ①）。

- 长文档分块 / Map-Reduce：不整篇塞上下文；保留条款编号 + 原文位置。
- 全局关联步骤（必须）：分块抽取后合并、再通读一次，生成全合同级 definitions 与
  crossReferences（分块会切碎"定义在第 1 页、引用在第 10 页"的跨页关联）。
- 异常条款兜底（附件/表格/"此处空白"），不使整条链路崩溃。
- 输出校验/修复：schema 校验失败 → 带错误信息重试 1 次 → 仍失败跳过该条。
- mock/规则模式（无 key）：正则条款切分 + 关键数值/引用正则抽取（确定性，链路自检）。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import ValidationError

from ..config import SCHEMA_MAX_RETRIES, using_mock
from ..llm import BalanceError, LLMClient, LLMError
from ..patterns import AMOUNT_RE, CLAUSE_TOP_RE, DAYS_RE, PCT_RE, REF_RE, SUB_NUM_RE
from ..state import ClauseFact, ContractState

_MONTHS_RE = re.compile(r"(\d+)\s*(?:个)?月")
_BLANK_RE = re.compile(r"此处\s*空白|暂无|无内容|^$", re.M)

EXTRACT_SYSTEM = (
    "你是合同条款抽取器。从给定的合同文本块中抽取条款事实卡片。\n"
    "输出严格 JSON：{\"clauses\": [{\"clauseId\": \"条款编号，如 12.3 或 第5条\", "
    "\"quote\": \"条款标题+原文关键句（≤500字，保留关键数值与条件）\", "
    "\"location\": \"原文位置/页码\", "
    "\"keyNumbers\": {\"amount\": 0, \"paymentDays\": 0, \"penaltyRatio\": 0.0, \"defectDays\": 0, \"warrantyMonths\": 0}, "
    "\"definitions\": {\"术语\": \"定义出处\"}}]}\n"
    "规则：\n"
    "- quote 必须包含条款标题（如'第八条 保密：…'），便于后续按标题识别条款类型；\n"
    "- 条款内容为'此处空白'/明确留空的，照常输出 clauseId，quote 输出'[条款标题]（此处空白）'"
    "（缺失型风险需由后续 worker 识别）；\n"
    "- 纯附件标题/目录行不输出；无法识别的数值留空/省略；编号带小数点的按原样保留（如 7.2）。"
)

GLOBAL_LINK_SYSTEM = (
    "你是合同条款全局关联器。以下是合并后的全部条款事实。"
    "输出严格 JSON：{\"definitions\": {\"术语\": \"定义所在 clauseId\"}, "
    "\"crossReferences\": [{\"from\": \"clauseId\", \"to\": \"clauseId\", \"reason\": \"简述\"}]}\n"
    "找出：1) 术语定义条款被其他条款使用的跨页关联；2) 条款间互相引用（'按第X条执行'、'详见第X条'等）。"
    "只输出有把握的关联，无则输出空对象/空数组。"
)


# ---------------------------------------------------------------- 切分（确定性）
def split_into_chunks(text: str, max_chars: int = 4000) -> list[str]:
    """按条款结构切块；条款结构缺失时按行/字符兜底分块。"""
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []
    # 主切分：第X条
    matches = list(CLAUSE_TOP_RE.finditer(text))
    if len(matches) >= 2:
        chunks = []
        for i, m in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            chunks.append(text[m.start() : end].strip())
        return _coalesce(chunks, max_chars)
    # 次切分：X.Y 编号
    sub = list(SUB_NUM_RE.finditer(text))
    if len(sub) >= 2:
        chunks = []
        for i, m in enumerate(sub):
            end = sub[i + 1].start() if i + 1 < len(sub) else len(text)
            chunks.append(text[m.start() : end].strip())
        return _coalesce(chunks, max_chars)
    # 兜底：按空行分块再合并
    paras = [p.strip() for p in text.split("\n") if p.strip()]
    return _coalesce(paras, max_chars)


def _coalesce(chunks: list[str], max_chars: int) -> list[str]:
    merged: list[str] = []
    buf = ""
    for c in chunks:
        if len(buf) + len(c) <= max_chars:
            buf = f"{buf}\n{c}" if buf else c
        else:
            if buf:
                merged.append(buf)
            buf = c
    if buf:
        merged.append(buf)
    return merged


def _first_clause_id(chunk: str) -> str:
    m = CLAUSE_TOP_RE.search(chunk) or SUB_NUM_RE.search(chunk)
    if m:
        return m.group(1)
    return _stable_clause_id(chunk)


def _stable_clause_id(text: str) -> str:
    """无条款号兜底：跨运行稳定哈希（S4：str hash 按进程加盐，不可复现）。"""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"c{int(digest[:8], 16) % 100000}"


# ---------------------------------------------------------------- mock/规则模式抽取
def _rule_extract(chunk: str) -> list[ClauseFact]:
    """正则抽取：条款号 + 原文 + 关键数值 + 引用（确定性，mock 链路自检用）。"""
    facts: list[ClauseFact] = []
    # 逐条切分（第X条 或 X.Y 编号）
    tops = list(CLAUSE_TOP_RE.finditer(chunk))
    if tops:
        segs: list[tuple[str, str]] = []
        for i, m in enumerate(tops):
            end = tops[i + 1].start() if i + 1 < len(tops) else len(chunk)
            segs.append((m.group(1), chunk[m.start() : end]))
    else:
        subs = list(SUB_NUM_RE.finditer(chunk))
        if subs:
            segs = []
            for i, m in enumerate(subs):
                end = subs[i + 1].start() if i + 1 < len(subs) else len(chunk)
                segs.append((m.group(1), chunk[m.start() : end]))
        else:
            segs = [(_first_clause_id(chunk), chunk)]
    for cid, seg in segs:
        seg = seg.strip()
        if not seg:
            continue
        cid = cid if cid.startswith("第") else f"第{cid}条"  # clauseId 规范化（与引用正则一致）
        if _BLANK_RE.search(seg) and len(seg) < 30:
            # 缺失型条款保留（带标题），供 worker 识别缺失风险；quote 截断
            facts.append(ClauseFact(clauseId=cid, quote=seg[:120], location="", keyNumbers={},
                                    definitions={}, references=[]))
            continue
        kn: dict[str, float | str] = {}
        for m in AMOUNT_RE.finditer(seg):
            try:
                kn["amount"] = float(m.group(1).replace(",", ""))
                break
            except ValueError:
                pass
        # 付款期限取条款内最大天数（"预付款 7 日内 + 余款 120 日内" → 120），与规则基线口径一致
        pay_days = [
            float(m.group(1))
            for m in DAYS_RE.finditer(seg)
            if "付" in seg or "款" in seg or "验收" in seg or "检验" in seg
        ]
        if pay_days:
            kn["paymentDays"] = max(pay_days)
        for m in PCT_RE.finditer(seg):
            kn.setdefault("penaltyRatio", float(m.group(1)))
        for m in _MONTHS_RE.finditer(seg):
            if "质保" in seg or "保修" in seg or "保证期" in seg:
                kn.setdefault("warrantyMonths", float(m.group(1)))
        refs: list[str] = []
        for m in REF_RE.finditer(seg):
            ref = f"第{m.group(1)}条"
            if ref not in refs:
                refs.append(ref)
        facts.append(
            ClauseFact(
                clauseId=cid,
                quote=seg[:500],
                location="",
                keyNumbers=kn,
                definitions={},
                references=refs,
            )
        )
    return facts


# ---------------------------------------------------------------- LLM 抽取（map）
def _llm_extract_chunk(llm: LLMClient, chunk: str) -> list[ClauseFact]:
    prompt = (
        "合同文本块：\n```\n" + chunk[:3500] + "\n```\n请抽取条款事实，输出 JSON。"
    )
    for attempt in range(SCHEMA_MAX_RETRIES + 1):
        try:
            data = llm.chat_json(
                [
                    {"role": "system", "content": EXTRACT_SYSTEM},
                    {"role": "user", "content": prompt},
                ]
            )
            items = data.get("clauses", []) if isinstance(data, dict) else []
            facts: list[ClauseFact] = []
            for it in items:
                try:
                    facts.append(ClauseFact(**{k: v for k, v in it.items() if k in ClauseFact.model_fields}))
                except ValidationError:
                    continue  # 单条坏数据跳过，不崩链
            return facts
        except BalanceError:  # 余额耗尽：全局性错误，冒泡显式失败（防输出空报告误导）
            raise
        except (LLMError, json.JSONDecodeError) as e:
            if attempt < SCHEMA_MAX_RETRIES:
                continue
            raise
    return []


# ---------------------------------------------------------------- 全局关联（reduce）
def _global_link(llm: LLMClient, facts: list[ClauseFact]) -> tuple[dict[str, str], list[tuple[str, str, str]]]:
    summary = "\n".join(f"{f.clauseId}: {f.quote[:200]}" for f in facts)
    prompt = f"全部条款事实：\n{summary}\n输出全局关联 JSON。"
    for attempt in range(SCHEMA_MAX_RETRIES + 1):
        try:
            data = llm.chat_json(
                [
                    {"role": "system", "content": GLOBAL_LINK_SYSTEM},
                    {"role": "user", "content": prompt},
                ]
            )
            defs = data.get("definitions", {}) if isinstance(data, dict) else {}
            refs = [
                (r.get("from", ""), r.get("to", ""), r.get("reason", ""))
                for r in (data.get("crossReferences", []) if isinstance(data, dict) else [])
                if isinstance(r, dict)
            ]
            return dict(defs), refs
        except BalanceError:  # 余额耗尽：冒泡（同上）
            raise
        except (LLMError, json.JSONDecodeError):
            if attempt < SCHEMA_MAX_RETRIES:
                continue
            break
    return {}, []


def _rule_global_link(facts: list[ClauseFact]) -> tuple[dict[str, str], list[tuple[str, str, str]]]:
    """mock 全局关联：用规则正则补引用（确定性）。"""
    defs: dict[str, str] = {}
    refs: list[tuple[str, str, str]] = []
    # 引用匹配：clauseId 文本中提到的其他"第X条"→ 目标 clauseId
    id_map = {f.clauseId: f for f in facts}
    for f in facts:
        for m in REF_RE.finditer(f.quote):
            target = f"第{m.group(1)}条"
            if target in id_map and target != f.clauseId:
                refs.append((f.clauseId, target, "引用"))
    return defs, refs


# ---------------------------------------------------------------- 节点
def build_extract_node(llm: LLMClient | None = None):
    """① 条款抽取节点：写黑板 clauseFacts。llm 由闭包注入（不放入 LangGraph state）。"""

    def node(state: ContractState) -> dict:
        text = state.get("contract_text", "")
        chunks = split_into_chunks(text)
        facts: list[ClauseFact] = []
        errors: list[str] = list(state.get("errors", []))

        if using_mock():
            for c in chunks:
                facts.extend(_rule_extract(c))
            defs, refs = _rule_global_link(facts)
        else:
            if llm is None:
                raise RuntimeError("extract_node requires LLMClient (non-mock)")
            for c in chunks:
                try:
                    facts.extend(_llm_extract_chunk(llm, c))
                except LLMError as e:
                    errors.append(f"extract chunk failed: {e}")
            defs, refs = _global_link(llm, facts)

        # 全局关联写回（跨页关联修复）
        for f in facts:
            f.definitions = {k: v for k, v in defs.items() if v == f.clauseId}
            f.references = [t for (fr, t, _r) in refs if fr == f.clauseId]
        # 合并跨 chunk 重复条款（同 clauseId 保留更完整 quote）
        merged: dict[str, ClauseFact] = {}
        for f in facts:
            if f.clauseId in merged:
                if len(f.quote) > len(merged[f.clauseId].quote):
                    merged[f.clauseId] = f
            else:
                merged[f.clauseId] = f
        return {"clauses": list(merged.values()), "errors": errors}

    return node
