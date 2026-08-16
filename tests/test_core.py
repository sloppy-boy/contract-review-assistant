"""核心逻辑单元测试（pytest）：黑板 reducer / 抽取 / 规则基线 / 计分 / 预算裁剪。

运行：python -m pytest tests/ -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.legal.rule_checker import rule_baseline_findings  # noqa: E402
from app.nodes.extract import split_into_chunks, _rule_extract  # noqa: E402
from app.nodes.workers import _clip_context  # noqa: E402
from app.state import ClauseFact, Finding, LegalBasis, findings_reducer  # noqa: E402
from eval.score import normalize_clause_id, score_contract  # noqa: E402


def mk_finding(id, clause, rtype, sev, evidence="", status="proposed"):
    return Finding(
        id=id, worker="w_test", clauseId=clause, clauseQuote="quote",
        riskType=rtype, severity=sev, legalBasis=LegalBasis(),
        evidence=evidence, suggestion="suggestion", status=status,
    )


# ================================================================ findings reducer
class TestFindingsReducer:
    def test_dedup_same_triplet_keeps_richer_evidence(self):
        """同条款+同类型+同严重度：合并，保留 evidence 更充分者（配置无关）。"""
        a = mk_finding("w1", "第五条", "违约金过高", "high", evidence="short")
        b = mk_finding("w2", "第五条", "违约金过高", "high", evidence="longer evidence here")
        merged = findings_reducer([], [a, b])
        assert len(merged) == 1
        assert merged[0].id == "w2"  # evidence 更长者胜出

    def test_ablation_a_cross_worker_collision_merges(self):
        """A 档（无复核）：跨 worker 撞车退化为合并（防重复计数虚增误报）。"""
        a = mk_finding("w_rule-x", "第六条", "定金条款违规", "medium", evidence="e1")
        b = mk_finding("w_rule-y", "第六条", "定金条款违规", "medium", evidence="e2 longer")
        merged = findings_reducer([], [a, b])
        assert len(merged) == 1
        assert merged[0].id == "w_rule-y"

    def test_review_status_update_by_id(self):
        """复核状态流转：按 id 覆盖 proposed→disputed，不被去重规则吞掉。"""
        f = mk_finding("w1", "第五条", "违约金过高", "high", evidence="e")
        state = findings_reducer([], [f])
        f.status = "disputed"
        f.reVerifyJustification = "有证据坚持"
        state2 = findings_reducer(state, [f])
        assert len(state2) == 1
        assert state2[0].status == "disputed"
        assert state2[0].reVerifyJustification == "有证据坚持"

    def test_distinct_triplets_keep_both(self):
        a = mk_finding("w1", "第五条", "违约金过高", "high")
        b = mk_finding("w2", "第五条", "违约金过高", "medium")  # 严重度不同
        merged = findings_reducer([], [a, b])
        assert len(merged) == 2


# ================================================================ 抽取
class TestExtract:
    def test_split_by_chinese_clause(self):
        # 短文档整篇合并是设计行为（放得下就不分块）；超长条款验证分块
        text = "第一条 标的\n" + "内容内容" * 2500 + "\n第二条 付款\n" + "内容内容" * 2500
        chunks = split_into_chunks(text)
        assert len(chunks) >= 2
        assert chunks[0].startswith("第一条")
        assert chunks[1].startswith("第二条")

    def test_split_by_number_clause(self):
        text = "1. 标的\n" + "内容内容" * 2500 + "\n2. 付款\n" + "内容内容" * 2500
        chunks = split_into_chunks(text)
        assert len(chunks) >= 2
        assert chunks[1].startswith("2.")

    def test_blank_clause_preserved(self):
        """缺失型条款保留（带标题），供 worker 识别缺失风险。"""
        text = "第八条 保密\n（此处空白）\n第九条 不可抗力\n标准条款内容"
        facts = _rule_extract(text)
        cids = [f.clauseId for f in facts]
        assert "第八条" in cids
        blank = next(f for f in facts if f.clauseId == "第八条")
        assert "空白" in blank.quote

    def test_rule_extract_key_numbers(self):
        text = "第四条 付款\n合同签订后 7 日内支付预付款 30%，验收合格后 120 日内支付余款。"
        facts = _rule_extract(text)
        assert len(facts) == 1
        assert facts[0].keyNumbers.get("paymentDays") == 120.0


# ================================================================ 规则基线
class TestRuleBaseline:
    def test_penalty_over_30(self):
        c = ClauseFact(clauseId="第五条", quote="任何一方违约的，支付合同总价 40% 的违约金。")
        fs = rule_baseline_findings([c])
        assert any(f.riskType == "违约金过高" and f.severity == "high" for f in fs)

    def test_deposit_over_20(self):
        c = ClauseFact(clauseId="第六条", quote="定金为合同总价的 25%。")
        fs = rule_baseline_findings([c])
        assert any(f.riskType == "定金条款违规" for f in fs)

    def test_arbitration_without_org(self):
        c = ClauseFact(clauseId="第十条", quote="协商不成的，提交仲裁解决。")
        fs = rule_baseline_findings([c])
        assert any("仲裁" in f.riskType for f in fs)

    def test_clean_clause_no_findings(self):
        """干净条款（违约金 10%、付款 30 天）不触发朴素规则。"""
        clauses = [
            ClauseFact(clauseId="第四条", quote="验收合格后 30 日内支付余款。"),
            ClauseFact(clauseId="第五条", quote="违约方支付合同总价 10% 的违约金。"),
            ClauseFact(clauseId="第十条", quote="协商不成的，向甲方所在地人民法院起诉。"),
        ]
        fs = rule_baseline_findings(clauses)
        assert fs == []


# ================================================================ 计分
class TestScoring:
    def test_normalize_clause_id(self):
        assert normalize_clause_id("第三条") == "3"
        assert normalize_clause_id("第十二条") == "12"
        assert normalize_clause_id("3") == "3"
        assert normalize_clause_id("第 5 条") == "5"

    def test_strict_hit(self):
        label = {"contractId": "x", "defects": [{"clauseId": "第五条", "riskType": "违约金过高", "severity": "high"}]}
        report = {"risks": [{"clauseId": "第五条", "riskType": "违约金过高", "severity": "high"}]}
        sc = score_contract(label, report)
        assert sc["hits"] == 1
        assert sc["partialScore"] == 1.0

    def test_partial_score_clause_only(self):
        """条款对但类型/严重度错：部分分 0.4。"""
        label = {"contractId": "x", "defects": [{"clauseId": "第五条", "riskType": "违约金过高", "severity": "high"}]}
        report = {"risks": [{"clauseId": "第五条", "riskType": "其他类型", "severity": "low"}]}
        sc = score_contract(label, report)
        assert sc["hits"] == 0
        assert abs(sc["partialScore"] - 0.4) < 1e-6

    def test_no_false_positive_double_count(self):
        """同一缺陷不因模型重复输出而重复命中。"""
        label = {"contractId": "x", "defects": [{"clauseId": "第五条", "riskType": "违约金过高", "severity": "high"}]}
        report = {"risks": [
            {"clauseId": "第五条", "riskType": "违约金过高", "severity": "high"},
            {"clauseId": "第五条", "riskType": "违约金过高", "severity": "high"},
        ]}
        sc = score_contract(label, report)
        assert sc["hits"] == 1  # 只计 1 次命中


# ================================================================ worker 预算裁剪
class TestBudget:
    def test_clip_respects_budget(self):
        blocks = ["A" * 3000, "B" * 3000, "C" * 3000]
        out = _clip_context(blocks, 5000)
        assert len(out) < len("".join(blocks))
        assert "A" in out and "B" not in out and "C" not in out  # 预算只装得下第一块

    def test_worker_input_within_8k(self):
        """WORKER_INPUT_BUDGET_TOKENS=8000 → 裁剪预算 12000 字符。"""
        blocks = [f"第{i}条：{'合同条款内容' * 200}" for i in range(20)]
        out = _clip_context(blocks, 8000 * 1.5)
        assert len(out) <= 8000 * 1.5
