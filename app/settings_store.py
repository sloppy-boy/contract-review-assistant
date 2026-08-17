"""运行时设置存储：多供应商 API 配置 + 审查/复核模型路由（前端"设置"页读写）。

背景：合同审查流水线需要两个模型——主链路（抽取/worker/报告，通常非 thinking 档）
与复核（对抗复核，thinking 档）。二者都应是"可配置"的：供应商（baseUrl+apiKey）、
模型名、单价都可独立选择，且切换即时生效（run_pipeline 每次从本存储读配置）。

存储文件：<ROOT>/settings.json（含 API key，已 gitignore）。
结构：
{
  "providers": {
    "deepseek":   {"baseUrl": "...", "apiKey": "...", "models": [...], "priceIn": 2.0, "priceOut": 8.0},
    "opencode-go":{"baseUrl": "...", "apiKey": "...", "models": [...], "priceIn": 1.0, "priceOut": 2.0}
  },
  "mainModel":   {"provider": "deepseek", "model": "deepseek-chat"},
  "reviewModel": {"provider": "deepseek", "model": "deepseek-reasoner"}
}

- apiKey 为空字符串 = 未配置（运行时将回退 .env 的 DEEPSEEK_API_KEY 作为 deepseek 默认）。
- 保存时 apiKey 传空 = 保留原值（前端不回显已存 key 明文）。
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from .config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEEPSEEK_PRICE_IN,
    DEEPSEEK_PRICE_OUT,
    DEEPSEEK_REVIEWER_MODEL,
    DEEPSEEK_REVIEWER_PRICE_IN,
    DEEPSEEK_REVIEWER_PRICE_OUT,
    ROOT,
)

SETTINGS_PATH = Path(os.environ.get("CONTRACT_REVIEW_SETTINGS", ROOT / "settings.json"))
if not SETTINGS_PATH.is_absolute():
    SETTINGS_PATH = ROOT / SETTINGS_PATH

_lock = threading.Lock()

# opencode-go（OpenCode Zen Go）预置模型目录（来自 pi-ai catalog，离线可用作前端下拉兜底）
OPENCODE_GO_MODELS = [
    "minimax-m3", "minimax-m2.7", "minimax-m2.5",
    "kimi-k3", "kimi-k2.7-code", "kimi-k2.6", "kimi-k2.5",
    "glm-5.2", "glm-5.3", "glm-5.1", "glm-5",
    "deepseek-v4-pro", "deepseek-v4-flash",
    "qwen3.8-max", "qwen3.7-max", "qwen3.7-plus", "qwen3.6-plus", "qwen3.5-plus",
    "mimo-v2-pro", "mimo-v2-omni", "mimo-v2.5-pro", "mimo-v2.5",
    "hy3", "hy3-preview", "gpt-5.6-luna", "grok-4.5",
]


def _defaults() -> dict:
    """默认设置：deepseek 用 .env 值；opencode-go 用官方端点（key 需用户填写）。"""
    return {
        "providers": {
            "deepseek": {
                "baseUrl": DEEPSEEK_BASE_URL,
                "apiKey": DEEPSEEK_API_KEY,  # 从 .env 继承，settings.json 保存后覆盖
                "models": ["deepseek-chat", "deepseek-reasoner"],
                "priceIn": DEEPSEEK_PRICE_IN,
                "priceOut": DEEPSEEK_PRICE_OUT,
            },
            "opencode-go": {
                "baseUrl": "https://opencode.ai/zen/go/v1",
                "apiKey": "",
                "models": list(OPENCODE_GO_MODELS),
                "priceIn": 1.0,   # deepseek-v4-flash 档约 ¥1.0/百万（估算）——可在设置页修改
                "priceOut": 2.0,
            },
        },
        "mainModel": {"provider": "deepseek", "model": DEEPSEEK_MODEL},
        "reviewModel": {
            "provider": "deepseek",
            "model": DEEPSEEK_REVIEWER_MODEL,
            "priceIn": DEEPSEEK_REVIEWER_PRICE_IN,
            "priceOut": DEEPSEEK_REVIEWER_PRICE_OUT,
        },
        # 通用设置（settings 页可改；env 优先，settings.json 兜底，重启后端生效）
        "common": {
            "reviewMode": os.environ.get("REVIEW_MODE", "C").upper(),
            "workerBudgetTokens": int(os.environ.get("WORKER_INPUT_BUDGET_TOKENS", "8000")),
            "topKArticles": int(os.environ.get("TOP_K_ARTICLES", "3")),
            "maxUploadChars": int(os.environ.get("MAX_UPLOAD_CHARS", "200000")),
            "balanceThreshold": float(os.environ.get("BALANCE_WARN_THRESHOLD", "5.0")),
        },
    }


def load_settings() -> dict:
    """读取运行时设置（settings.json 优先，缺省回退默认；deepseek key 空时继承 .env）。"""
    base = _defaults()
    if SETTINGS_PATH.exists():
        try:
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            base = _deep_merge(base, saved)
        except (json.JSONDecodeError, OSError):
            pass  # 损坏则回退默认
    # 空 key 的 deepseek → 继承 .env（用户没在设置页填时仍可用）
    prov = base.setdefault("providers", {})
    ds = prov.get("deepseek")
    if ds and not ds.get("apiKey") and DEEPSEEK_API_KEY:
        ds["apiKey"] = DEEPSEEK_API_KEY
    return base


def save_settings(new: dict) -> dict:
    """保存运行时设置（合并写入）。apiKey 传空字符串 = 保留原值。"""
    with _lock:
        cur = load_settings()
        provs = cur.setdefault("providers", {})
        incoming = new.get("providers") or {}
        for pid, p in incoming.items():
            old = provs.setdefault(
                pid,
                {"baseUrl": "", "apiKey": "", "models": [], "priceIn": 1.0, "priceOut": 2.0},
            )
            if "baseUrl" in p and p["baseUrl"]:
                old["baseUrl"] = p["baseUrl"].rstrip("/")
            if p.get("apiKey"):  # 空 = 保留原 key
                old["apiKey"] = p["apiKey"].strip()
            if p.get("models"):
                old["models"] = p["models"]
            if "priceIn" in p:
                old["priceIn"] = float(p.get("priceIn") or old.get("priceIn", 1.0))
            if "priceOut" in p:
                old["priceOut"] = float(p.get("priceOut") or old.get("priceOut", 2.0))
        if "mainModel" in new and isinstance(new["mainModel"], dict):
            cur["mainModel"] = new["mainModel"]
        if "reviewModel" in new and isinstance(new["reviewModel"], dict):
            cur["reviewModel"] = new["reviewModel"]
        if "common" in new and isinstance(new["common"], dict):
            common = cur.setdefault("common", {})
            for k, v in new["common"].items():
                if v is not None and v != "":
                    common[k] = v
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
        return cur


def public_settings() -> dict:
    """给前端的脱敏视图：apiKey 只暴露 hasKey 布尔。"""
    s = load_settings()
    provs_out = {}
    for pid, p in (s.get("providers") or {}).items():
        provs_out[pid] = {
            "baseUrl": p.get("baseUrl", ""),
            "hasKey": bool(p.get("apiKey")),
            "models": p.get("models", []),
            "priceIn": p.get("priceIn", 1.0),
            "priceOut": p.get("priceOut", 2.0),
        }
    return {
        "providers": provs_out,
        "mainModel": s.get("mainModel", {}),
        "reviewModel": s.get("reviewModel", {}),
        "common": s.get("common", {}),
    }


def active_llm_config(role: str) -> dict | None:
    """按角色（main/review）解析生效的 LLM 配置：{provider, baseUrl, apiKey, model, priceIn, priceOut}。

    模型/供应商缺失或 apiKey 为空 → None（调用方走 mock 降级）。
    """
    s = load_settings()
    role_key = "mainModel" if role == "main" else "reviewModel"
    slot = s.get(role_key) or {}
    provider = slot.get("provider") or ""
    prov = (s.get("providers") or {}).get(provider)
    if not prov or not prov.get("apiKey"):
        return None
    if role == "review":
        price_in = slot.get("priceIn") or prov.get("priceIn", 1.0)
        price_out = slot.get("priceOut") or prov.get("priceOut", 2.0)
    else:
        price_in = prov.get("priceIn", 1.0)
        price_out = prov.get("priceOut", 2.0)
    return {
        "provider": provider,
        "baseUrl": prov.get("baseUrl", ""),
        "apiKey": prov.get("apiKey", ""),
        "model": slot.get("model") or (prov.get("models") or [""])[0],
        "priceIn": price_in,
        "priceOut": price_out,
    }


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并（override 优先；providers 按 id 合并，非整体替换）。"""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out