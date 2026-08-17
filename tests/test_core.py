"""核心逻辑单元测试（pytest）：黑板 reducer / 抽取 / 规则基线 / 计分 / 预算裁剪。

运行：python -m pytest tests/ -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.api import _sanitize_filename  # noqa: E402
from app.legal.rule_checker import rule_baseline_findings  # noqa: E402
from app.llm import BalanceError, LLMError, classify_balance_error  # noqa: E402
from app.nodes.extract import _llm_extract_chunk, _stable_clause_id, split_into_chunks, _rule_extract  # noqa: E402
from app.nodes.report import report_node  # noqa: E402
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

    def test_hit_fields_count_partial_two_field_hit(self):
        """S14：条款+严重度命中（0.7 分）时，severity 命中必须计数（不再被反推漏计）。"""
        label = {"contractId": "x", "defects": [{"clauseId": "第五条", "riskType": "违约金过高", "severity": "high"}]}
        report = {"risks": [{"clauseId": "第五条", "riskType": "其他类型", "severity": "high"}]}
        sc = score_contract(label, report)
        assert abs(sc["partialScore"] - 0.7) < 1e-6
        assert sc["hitFields"]["clause"] == 1
        assert sc["hitFields"]["severity"] == 1   # 0.7 分时（类型未中）severity 仍计数
        assert sc["hitFields"]["riskType"] == 0


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


# ================================================================ schema 校验降级（P2 单测补全）
class _FakeLLM:
    """假 LLM 客户端：按预置响应序列应答 chat_json（用于 schema 降级单测）。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def chat_json(self, messages, temperature=0.1):
        self.calls += 1
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


class TestSchemaDegrade:
    def test_extract_bad_items_skipped(self):
        """LLM 返回的坏条目被 ValidationError 跳过，不崩链（schema 校验降级）。"""
        fake = _FakeLLM([{"clauses": [{"clauseId": "第一条", "quote": "标的物…"}, {"bad": 1}]}])
        facts = _llm_extract_chunk(fake, "第一条 标的")
        assert len(facts) == 1
        assert facts[0].clauseId == "第一条"

    def test_extract_retry_then_success(self):
        """schema 解析失败（LLMError）→ 重试 1 次成功（SCHEMA_MAX_RETRIES=1）。"""
        fake = _FakeLLM([LLMError("bad json"), {"clauses": [{"clauseId": "第一条", "quote": "x"}]}])
        facts = _llm_extract_chunk(fake, "第一条 标的")
        assert len(facts) == 1
        assert fake.calls == 2  # 恰好重试 1 次

    def test_extract_total_failure_raises_for_caller_degrade(self):
        """重试仍失败 → 抛 LLMError；由 nodes 层调用方降级（跳过该 chunk，不崩全链）。"""
        fake = _FakeLLM([LLMError("bad1"), LLMError("bad2")])
        with pytest.raises(LLMError):
            _llm_extract_chunk(fake, "第一条 标的")
        assert fake.calls == 2  # 初始 1 次 + 重试 1 次


# ================================================================ 稳定哈希 / 文件名消毒 / 条款导航
class TestStabilityFixes:
    def test_clause_id_fallback_stable_across_runs(self):
        """S4：无条款号兜底 clauseId 用稳定哈希（跨运行一致，非进程加盐 str hash）。"""
        text = "无条款编号的合同段落……" * 3
        a = _stable_clause_id(text)
        b = _stable_clause_id(text)
        assert a == b
        assert a.startswith("c")
        assert a != _stable_clause_id(text + "不同")

    def test_sanitize_filename_strips_header_unsafe(self):
        """S1：合同名中的引号/换行/控制字符被替换（防 Content-Disposition 头注入）。"""
        assert _sanitize_filename('合同"名称"\n结算单') == "合同_名称__结算单"
        assert '"' not in _sanitize_filename('a"b')
        assert "\n" not in _sanitize_filename("a\nb")
        assert _sanitize_filename("") == "contract-review-report"  # 空名回退
        assert _sanitize_filename(None) == "contract-review-report"

    def test_clause_nav_takes_highest_severity(self):
        """S8：同条款多个 findings 时，条款导航取最高严重度（不丢更严重档）。"""
        clauses = [ClauseFact(clauseId="第五条", quote="违约金条款内容")]
        findings = [
            mk_finding("f1", "第五条", "违约金过高", "low", status="upheld"),
            mk_finding("f2", "第五条", "违约金过高", "high", status="upheld"),
        ]
        out = report_node(
            {
                "findings": findings,
                "clauses": clauses,
                "contract_name": "x",
                "contract_type": "purchase",
            },
            mode="B",
        )
        nav = out["report"]["clauses"]
        assert nav[0]["riskLevel"] == "high"

    def test_clause_nav_none_when_no_risk(self):
        """无风险条款的导航项 riskLevel 为 None。"""
        clauses = [ClauseFact(clauseId="第一条", quote="标的")]
        out = report_node(
            {"findings": [], "clauses": clauses, "contract_name": "x", "contract_type": "purchase"},
            mode="B",
        )
        assert out["report"]["clauses"][0]["riskLevel"] is None


# ================================================================ 余额预警 / 停止服务
class TestBalanceGuard:
    def test_classify_402_as_balance_error(self):
        """402 Payment Required → BalanceError（DeepSeek 余额耗尽标准码）。"""
        err = classify_balance_error(402, '{"error":{"message":"insufficient balance"}}')
        assert isinstance(err, BalanceError)
        assert "余额" in str(err)

    def test_classify_balance_keyword_in_body(self):
        """非 402 但响应体含余额关键词（如 429 限流中夹带欠费说明）→ BalanceError。"""
        assert isinstance(
            classify_balance_error(429, '{"error":{"message":"insufficient_balance"}}'), BalanceError
        )
        assert isinstance(classify_balance_error(500, '{"error":"欠费"}'), BalanceError)
        assert isinstance(classify_balance_error(403, "account suspended"), BalanceError)

    def test_classify_normal_errors_not_balance(self):
        """普通 4xx/5xx/限流（无余额关键词）→ None（不误判，429/5xx 仍走重试）。"""
        assert classify_balance_error(429, "rate limit exceeded") is None
        assert classify_balance_error(500, "internal server error") is None
        assert classify_balance_error(401, "invalid api key") is None
        assert classify_balance_error(200, "ok") is None

    def test_balance_error_bubbles_through_extract(self):
        """余额耗尽在抽取节点必须冒泡（不能被部分成功降级吞掉 → 空报告）。"""
        fake = _FakeLLM([BalanceError("insufficient_balance")])
        with pytest.raises(BalanceError):
            _llm_extract_chunk(fake, "第一条 标的")
        assert fake.calls == 1  # 不重试即冒泡

    def test_balance_endpoint_mock_mode(self, monkeypatch):
        """mock 模式（强制 mock / 无 key）→ /balance 返回占位（前端不提示），不发外部请求。"""
        from fastapi.testclient import TestClient
        from app import api as api_mod

        monkeypatch.setenv("DSH_FORCE_MOCK", "1")
        monkeypatch.setattr(api_mod, "active_llm_config", lambda role: None)  # 无主供应商 → mock
        client = TestClient(api_mod.app)
        r = client.get("/balance")
        assert r.status_code == 200
        data = r.json()
        assert data["mock"] is True
        assert data["available"] is None and data["balance"] is None
        assert data["threshold"] > 0

    def test_balance_endpoint_real_parse(self, monkeypatch):
        """真实分支：正确解析 DeepSeek /user/balance 的 balance_infos（CNY 求和）。"""
        from fastapi.testclient import TestClient
        from app import api as api_mod

        deepseek_cfg = {
            "provider": "deepseek", "baseUrl": "https://api.deepseek.com/v1",
            "apiKey": "sk-test", "model": "deepseek-chat",
            "priceIn": 2.0, "priceOut": 8.0,
        }

        class _FakeResp:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "is_available": True,
                    "balance_infos": [
                        {"currency": "CNY", "total_balance": "12.30"},
                        {"currency": "USD", "total_balance": "5.00"},  # 非 CNY 不计
                    ],
                }

        class _FakeClient:
            def __init__(self, *a, **kw): pass

            def __enter__(self): return self

            def __exit__(self, *a): return False

            def get(self, url, headers=None):
                assert "/user/balance" in url and "Bearer" in headers["Authorization"]
                return _FakeResp()

        monkeypatch.setenv("DSH_FORCE_MOCK", "0")
        monkeypatch.setattr(api_mod, "using_mock", lambda: False)
        monkeypatch.setattr(api_mod, "active_llm_config", lambda role: deepseek_cfg)
        monkeypatch.setattr(api_mod.httpx, "Client", _FakeClient)
        client = TestClient(api_mod.app)
        r = client.get("/balance")
        assert r.status_code == 200
        data = r.json()
        assert data["mock"] is False
        assert data["available"] is True
        assert data["balance"] == 12.3  # 只计 CNY，忽略 USD

    def test_balance_endpoint_non_deepseek_provider_skips(self, monkeypatch):
        """非 DeepSeek 供应商（如 opencode-go 订阅制）→ 无余额接口，available=None（前端不提示）。"""
        from fastapi.testclient import TestClient
        from app import api as api_mod

        ocgo_cfg = {
            "provider": "opencode-go", "baseUrl": "https://opencode.ai/zen/go/v1",
            "apiKey": "sk-x", "model": "deepseek-v4-flash",
            "priceIn": 1.0, "priceOut": 2.0,
        }
        monkeypatch.setenv("DSH_FORCE_MOCK", "0")
        monkeypatch.setattr(api_mod, "using_mock", lambda: False)
        monkeypatch.setattr(api_mod, "active_llm_config", lambda role: ocgo_cfg)
        client = TestClient(api_mod.app)
        r = client.get("/balance")
        data = r.json()
        assert data["mock"] is False
        assert data["available"] is None
        assert "订阅制" in data.get("note", "")  # 前端据此不显示余额预警


# ================================================================ 设置存储（多供应商）
class TestSettingsStore:
    def test_save_and_public_hides_key(self, monkeypatch, tmp_path):
        """保存供应商配置后 public 视图脱敏（只暴露 hasKey，不泄露 key 明文）。"""
        from app import settings_store as ss

        monkeypatch.setattr(ss, "SETTINGS_PATH", tmp_path / "settings.json")
        ss.save_settings({
            "providers": {"opencode-go": {"baseUrl": "https://opencode.ai/zen/go/v1", "apiKey": "sk-secret-123"}},
            "mainModel": {"provider": "opencode-go", "model": "deepseek-v4-flash"},
        })
        pub = ss.public_settings()
        assert pub["providers"]["opencode-go"]["hasKey"] is True
        assert "sk-secret" not in __import__("json").dumps(pub)
        assert pub["mainModel"]["model"] == "deepseek-v4-flash"

    def test_empty_key_preserves_existing(self, monkeypatch, tmp_path):
        """apiKey 传空 = 保留原值（前端不回显已存密钥）。"""
        from app import settings_store as ss

        monkeypatch.setattr(ss, "SETTINGS_PATH", tmp_path / "settings.json")
        ss.save_settings({"providers": {"opencode-go": {"baseUrl": "https://opencode.ai/zen/go/v1", "apiKey": "sk-keep"}}})
        ss.save_settings({"providers": {"opencode-go": {"baseUrl": "https://opencode.ai/zen/go/v1", "apiKey": ""}}})
        s = ss.load_settings()
        assert s["providers"]["opencode-go"]["apiKey"] == "sk-keep"

    def test_active_config_none_without_key(self, monkeypatch, tmp_path):
        """供应商无 key → active_llm_config 返回 None（调用方走 mock，不烧 token）。"""
        from app import settings_store as ss

        monkeypatch.setattr(ss, "SETTINGS_PATH", tmp_path / "settings.json")
        ss.save_settings({
            "providers": {"opencode-go": {"baseUrl": "https://opencode.ai/zen/go/v1", "apiKey": ""}},
            "mainModel": {"provider": "opencode-go", "model": "deepseek-v4-flash"},
        })
        assert ss.active_llm_config("main") is None

    def test_common_settings_persist(self, monkeypatch, tmp_path):
        """通用设置（reviewMode/预算等）持久化并可读回。"""
        from app import settings_store as ss

        monkeypatch.setattr(ss, "SETTINGS_PATH", tmp_path / "settings.json")
        ss.save_settings({"common": {"reviewMode": "B", "workerBudgetTokens": 16000}})
        s = ss.load_settings()
        assert s["common"]["reviewMode"] == "B"
        assert s["common"]["workerBudgetTokens"] == 16000
