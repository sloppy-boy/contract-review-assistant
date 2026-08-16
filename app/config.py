"""全局配置：模型路由、输入预算、评测及格线、路径。

规则（SPEC 2.9 / PROMPT）：
- API key 只从环境变量读取（DEEPSEEK_API_KEY / SILICONFLOW_API_KEY），缺失时系统进入
  mock/规则降级模式（用于链路自检，绝不产出可汇报的评测数字）。
- 模型名全部配置化：文档中的 "v4-flash / thinking 档" 映射到真实 API 模型名
  （deepseek-chat / deepseek-reasoner），可通过环境变量覆盖。
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path | None = None) -> None:
    """轻量 .env 加载（无第三方依赖）：KEY=VALUE 行写入 os.environ（已有值不覆盖）。"""
    p = path or (ROOT / ".env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

# ---------------------------------------------------------------- API keys
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
SILICONFLOW_API_KEY = os.environ.get("SILICONFLOW_API_KEY", "").strip()

# ---------------------------------------------------------------- 模型路由
DEEPSEEK_BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
# 主链路（抽取/workers/报告）：v4-flash 档 → 官方 deepseek-chat
DEEPSEEK_MODEL = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
# 复核（thinking 档）→ 官方 deepseek-reasoner
DEEPSEEK_REVIEWER_MODEL = os.environ.get("DEEPSEEK_REVIEWER_MODEL", "deepseek-reasoner")

SILICONFLOW_BASE_URL = os.environ.get("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
# 法条嵌入（硬依赖：DeepSeek 无 embedding API，勿换）
EMBED_MODEL = os.environ.get("SILICONFLOW_EMBED_MODEL", "BAAI/bge-m3")
# 盲标第二模型（不同家族，防共偏）
LABEL_MODEL = os.environ.get("SILICONFLOW_LABEL_MODEL", "Qwen/Qwen3.5-122B-A10B")

# ---------------------------------------------------------------- 成本单价（元/百万 tokens）
DEEPSEEK_PRICE_IN = float(os.environ.get("DEEPSEEK_PRICE_IN", "2.0"))       # deepseek-chat 输入
DEEPSEEK_PRICE_OUT = float(os.environ.get("DEEPSEEK_PRICE_OUT", "8.0"))     # deepseek-chat 输出
DEEPSEEK_REVIEWER_PRICE_IN = float(os.environ.get("DEEPSEEK_REVIEWER_PRICE_IN", "4.0"))
DEEPSEEK_REVIEWER_PRICE_OUT = float(os.environ.get("DEEPSEEK_REVIEWER_PRICE_OUT", "16.0"))

# ---------------------------------------------------------------- 消融/复核
# A 无复核 / B 复核直滤 / C 复核+打回重证（SPEC 2.6 消融三档；及格线以 C 档为准）
REVIEW_MODE = os.environ.get("REVIEW_MODE", "C").strip().upper()
if REVIEW_MODE not in {"A", "B", "C"}:
    REVIEW_MODE = "C"
# 复核查证模式（默认开；可开关子项，SPEC 2.6）
VERIFY_MODE = os.environ.get("VERIFY_MODE", "1").strip() not in {"0", "false", "no"}

# ---------------------------------------------------------------- 输入硬预算（SPEC 2.3）
WORKER_INPUT_BUDGET_TOKENS = int(os.environ.get("WORKER_INPUT_BUDGET_TOKENS", "8000"))
TOP_K_ARTICLES = int(os.environ.get("TOP_K_ARTICLES", "3"))          # 法条只取 top-3
REF_SUMMARY_MAX_SENTENCES = int(os.environ.get("REF_SUMMARY_MAX_SENTENCES", "3"))  # 引用摘要 ≤3 句

# ---------------------------------------------------------------- LLM 调用
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "120"))
LLM_MAX_RETRIES = 2      # 超时/网络重试
SCHEMA_MAX_RETRIES = 1    # schema 解析失败重试 1 次（SPEC 2.9）
EMBED_CACHE_PATH = ROOT / ".cache" / "embed_cache.json"

# ---------------------------------------------------------------- 路径
DATASET_DIR = ROOT / "eval" / "dataset"
MANUAL_JSON_PATH = ROOT / "app" / "legal" / "manual_data.json"
REPORT_CACHE_DIR = ROOT / "frontend" / "public" / "reports"   # 离线演示缓存（真实导出）
DEMO_CONTRACT_DIR = ROOT / "eval" / "dataset" / "demo"        # 3 份演出合同

# ---------------------------------------------------------------- 评测（SPEC 2.7 及格线）
DEV_RATIO = 0.7
PASS_RECALL = 0.85          # 植入缺陷组召回率 ≥ 85%（C 档系统级）
PASS_FP_RATE = 0.15         # 干净组误报率 ≤ 15%（误报密度）
BEAT_BASELINE_POINTS = 10   # 赢规则基线：召回或 F1 ≥ +10 个点
PASS_COST_PER_CONTRACT = 1.0   # < 1 元/份
PASS_LATENCY_PER_CONTRACT = 60.0  # < 60s/份

# 部分分权重（辅助诊断，不进简历主数字，SPEC 2.6）
PARTIAL_WEIGHTS = {"clause": 0.4, "riskType": 0.3, "severity": 0.3}

# 人工抽样复核比例（SPEC 2.6）
MANUAL_AUDIT_RATIO = 0.2


def using_mock() -> bool:
    """无 key 时返回 True：系统进入规则降级 mock 模式（仅链路自检，数字无效）。
    DSH_FORCE_MOCK=1 可强制 mock（smoke_test 链路自检用，防误烧真实 token）。"""
    return (not DEEPSEEK_API_KEY) or os.environ.get("DSH_FORCE_MOCK", "") == "1"
