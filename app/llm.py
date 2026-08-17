"""LLM 客户端：DeepSeek API（OpenAI 兼容端点）。

- chat(): 通用对话，支持 json 模式（response_format={"type": "json_object"}）。
- 并发限流：全局信号量限制最大并发 API 调用（13 个 worker 扇出 + 多流水线并行时防 429）；
  429/5xx 指数退避重试（含随机抖动，尊重 Retry-After）。
- 超时重试 LLM_MAX_RETRIES 次；失败抛 LLMError，由调用方降级（部分成功原则）。
- mock 判定在 nodes 层完成（无 key 时走规则降级，不在此文件造假数据）。
"""
from __future__ import annotations

import json
import os
import random
import threading
import time
from typing import Any

import httpx

from .config import (
    DEEPSEEK_API_KEY,
    DEEPSEEK_BASE_URL,
    DEEPSEEK_MODEL,
    DEEPSEEK_REVIEWER_MODEL,
    LLM_MAX_RETRIES,
    LLM_TIMEOUT,
)

# 全局并发限流：限制所有 LLMClient 实例的总并发 API 调用数（进程级信号量）
MAX_CONCURRENT_LLM = int(os.environ.get("LLM_MAX_CONCURRENT", "8"))
_llm_semaphore = threading.Semaphore(MAX_CONCURRENT_LLM)


class LLMError(RuntimeError):
    """LLM 调用失败（重试后仍失败）。"""


class BalanceError(LLMError):
    """API 供应商余额不足 / 账户不可用（停止服务）。

    全局性错误：所有 LLM 调用都会失败，必须冒泡让任务显式失败并提示用户，
    不能走"部分成功"降级（否则输出空报告误导用户「无风险」）。
    """


class _Retryable(RuntimeError):
    """可重试错误（429 限流 / 5xx / 网络抖动）。"""


_BALANCE_HINTS = (
    "insufficient_balance",
    "insufficient balance",
    "balance not enough",
    "balance exhausted",
    "insufficient quota",
    "quota exceeded",
    "account suspended",
    "account disabled",
    "欠费",
    "余额不足",
    "余额用完",
    "账户已停用",
)


def classify_balance_error(status: int, body: str) -> BalanceError | None:
    """识别余额/配额类错误（402 Payment Required + 余额关键词）。返回 BalanceError 或 None。

    独立纯函数：便于单测；chat() 与上游可复用同一判定。
    """
    body_l = (body or "").lower()
    if status == 402:
        return BalanceError(f"API 供应商余额不足或停止服务（HTTP 402）: {body[:200]}")
    if status in (400, 401, 403, 429, 500, 502, 503, 504):
        for hint in _BALANCE_HINTS:
            if hint in body_l:
                return BalanceError(
                    f"API 供应商余额不足或停止服务（HTTP {status}）: {body[:200]}"
                )
    return None


class LLMClient:
    def __init__(
        self,
        api_key: str = DEEPSEEK_API_KEY,
        base_url: str = DEEPSEEK_BASE_URL,
        model: str = DEEPSEEK_MODEL,
        timeout: float = LLM_TIMEOUT,
        max_retries: int = LLM_MAX_RETRIES,
        price_in: float = 2.0,
        price_out: float = 8.0,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.price_in = price_in
        self.price_out = price_out
        self.total_input_tokens = 0
        self.total_output_tokens = 0
        self._usage_lock = threading.Lock()  # 13 个 worker 并发累加 token，防计数竞态
        self._client = httpx.Client(timeout=timeout) if api_key else None

    # ------------------------------------------------------------------ 基础调用
    def chat(
        self,
        messages: list[dict[str, str]],
        json_mode: bool = False,
        temperature: float = 0.2,
        max_tokens: int | None = None,
    ) -> str:
        """调用 chat completions，返回内容字符串。失败重试后抛 LLMError。"""
        if not self.api_key or self._client is None:
            raise LLMError("no api key: LLMClient 未配置，禁止在无 key 下调用")
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if max_tokens:
            payload["max_tokens"] = max_tokens

        last_err: Exception | None = None
        with _llm_semaphore:  # 全局并发限流（防 429）
            for attempt in range(self.max_retries + 1):
                try:
                    resp = self._client.post(
                        f"{self.base_url}/chat/completions",
                        headers={
                            "Authorization": f"Bearer {self.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    )
                    # 余额/配额类错误（402 或余额关键词）→ 直接失败并冒泡（全局性错误）
                    balance_err = classify_balance_error(resp.status_code, resp.text)
                    if balance_err is not None:
                        raise balance_err
                    # 限流/服务端错误 → 可重试
                    if resp.status_code in (429, 500, 502, 503, 504):
                        raise _Retryable(f"HTTP {resp.status_code}: {resp.text[:120]}")
                    resp.raise_for_status()
                    data = resp.json()
                    usage = data.get("usage") or {}
                    with self._usage_lock:
                        self.total_input_tokens += int(usage.get("prompt_tokens", 0) or 0)
                        self.total_output_tokens += int(usage.get("completion_tokens", 0) or 0)
                    return data["choices"][0]["message"]["content"]
                except BalanceError:  # 余额不足：原样冒泡（上游显式失败并提示）
                    raise
                except _Retryable as e:
                    last_err = e
                    if attempt < self.max_retries:
                        time.sleep(_backoff(attempt))
                except httpx.HTTPStatusError as e:  # 4xx（401/400 等）重试无意义，直接失败
                    raise LLMError(f"chat failed (HTTP {e.response.status_code}): {e.response.text[:120]}")
                except (httpx.TimeoutException, httpx.NetworkError) as e:  # 网络/超时 → 可重试
                    last_err = e
                    if attempt < self.max_retries:
                        time.sleep(_backoff(attempt))
                except Exception as e:  # 编程错误（KeyError/TypeError 等）不重试，暴露根因（S5）
                    raise LLMError(f"chat failed (non-retryable): {e!r}") from e
        raise LLMError(f"chat failed after {self.max_retries + 1} attempts: {last_err}")

    # ------------------------------------------------------------------ JSON 结构化调用
    def chat_json(self, messages: list[dict[str, str]], temperature: float = 0.1) -> dict:
        """要求模型输出 JSON 对象并解析。解析失败抛 LLMError（调用方负责 schema 重试/跳过）。"""
        text = self.chat(messages, json_mode=True, temperature=temperature)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 容忍 ```json ... ``` 包裹
            cleaned = text.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            return json.loads(cleaned)

    # ------------------------------------------------------------------ 便捷构造
    def reviewer(self) -> "LLMClient":
        """复核 thinking 档客户端（独立模型名与单价）。"""
        from .config import DEEPSEEK_REVIEWER_MODEL, DEEPSEEK_REVIEWER_PRICE_IN, DEEPSEEK_REVIEWER_PRICE_OUT

        return LLMClient(
            api_key=self.api_key,
            base_url=self.base_url,
            model=DEEPSEEK_REVIEWER_MODEL,
            timeout=self.timeout,
            max_retries=self.max_retries,
            price_in=DEEPSEEK_REVIEWER_PRICE_IN,
            price_out=DEEPSEEK_REVIEWER_PRICE_OUT,
        )

    def cost_yuan(self) -> float:
        """按实际 usage 计费（元）。"""
        return (
            self.total_input_tokens * self.price_in / 1e6
            + self.total_output_tokens * self.price_out / 1e6
        )

    def close(self) -> None:
        if self._client is not None:
            self._client.close()


def _backoff(attempt: int) -> float:
    """指数退避 + 抖动：1.5s → 3s → 6s（上限 30s），防多客户端同时重试打爆 API。"""
    return min(1.5 * (2 ** attempt) + random.uniform(0, 0.8), 30.0)
