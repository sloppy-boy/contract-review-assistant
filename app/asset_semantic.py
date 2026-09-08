"""Optional, local-only semantic ranking for pre-authorized asset citations.

The caller is responsible for ACL filtering before passing citations here.
The default encoder is intentionally unavailable until a local model directory
is configured with ``CRA_EMBEDDING_MODEL_PATH``.  Install the optional
``sentence-transformers`` dependency to use that default encoder.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np


MAX_QUERY_LENGTH = 2_000
MAX_CANDIDATES = 500
_REQUIRED_FIELDS = ("assetId", "title", "segmentId", "location", "text")
_MODEL_CACHE: dict[str, Any] = {}
_MODEL_CACHE_LOCK = threading.Lock()


def _default_encoder() -> Callable[[list[str]], Any]:
    configured_path = os.getenv("CRA_EMBEDDING_MODEL_PATH", "").strip()
    if not configured_path:
        raise RuntimeError(
            "语义检索暂不可用（503）：未配置本地模型目录 CRA_EMBEDDING_MODEL_PATH。"
        )

    model_path = Path(configured_path).expanduser().resolve()
    if not model_path.is_dir():
        raise RuntimeError(
            "语义检索暂不可用（503）：CRA_EMBEDDING_MODEL_PATH 必须指向本地模型目录。"
        )

    cache_key = os.fspath(model_path)
    with _MODEL_CACHE_LOCK:
        model = _MODEL_CACHE.get(cache_key)
        if model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except (ImportError, ModuleNotFoundError) as exc:
                raise RuntimeError(
                    "语义检索暂不可用（503）：请安装可选依赖 sentence-transformers。"
                ) from exc
            try:
                model = SentenceTransformer(cache_key, local_files_only=True)
            except Exception as exc:
                raise RuntimeError(
                    "语义检索暂不可用（503）：本地向量模型加载失败，请检查模型目录。"
                ) from exc
            _MODEL_CACHE[cache_key] = model

    return model.encode


def _validate_inputs(query: str, citations: list[dict], limit: int) -> str:
    if not isinstance(query, str):
        raise TypeError("query 必须是字符串。")
    normalized_query = query.strip()
    if not normalized_query:
        raise ValueError("query 不能为空。")
    if len(normalized_query) > MAX_QUERY_LENGTH:
        raise ValueError(f"query 不能超过 {MAX_QUERY_LENGTH} 个字符。")
    if not isinstance(citations, list):
        raise TypeError("citations 必须是列表。")
    if len(citations) > MAX_CANDIDATES:
        raise ValueError(f"候选引用不能超过 {MAX_CANDIDATES} 条。")
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise TypeError("limit 必须是正整数。")
    if limit <= 0:
        raise ValueError("limit 必须是正整数。")

    for citation in citations:
        if not isinstance(citation, dict):
            raise TypeError("每个候选引用必须是对象。")
        missing = [field for field in _REQUIRED_FIELDS if field not in citation]
        if missing:
            raise ValueError(f"候选引用缺少字段：{', '.join(missing)}。")
        for field in ("assetId", "title", "segmentId", "text"):
            if not isinstance(citation[field], str) or not citation[field].strip():
                raise ValueError(f"候选引用字段 {field} 必须是非空字符串。")
    return normalized_query


def _validated_vectors(raw_vectors: Any, expected_count: int) -> np.ndarray:
    try:
        vectors = np.asarray(raw_vectors, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError("编码器必须返回规则的数值向量。") from exc
    if vectors.ndim != 2 or vectors.shape[0] != expected_count or vectors.shape[1] == 0:
        raise ValueError("编码器向量输出数量或维度不正确。")
    if not np.isfinite(vectors).all():
        raise ValueError("编码器向量必须全部是有限实值。")
    norms = np.linalg.norm(vectors, axis=1)
    if not np.isfinite(norms).all() or np.any(norms == 0):
        raise ValueError("编码器向量不能是零向量。")
    return vectors / norms[:, np.newaxis]


def semantic_search(
    query: str,
    citations: list[dict],
    *,
    encoder: Callable[[list[str]], list[list[float]]] | None = None,
    limit: int = 20,
) -> list[dict]:
    """Rank pre-authorized citations by cosine similarity to ``query``."""

    normalized_query = _validate_inputs(query, citations, limit)
    if not citations:
        return []
    active_encoder = encoder or _default_encoder()
    if not callable(active_encoder):
        raise TypeError("encoder 必须可调用。")

    texts = [normalized_query, *(citation["text"] for citation in citations)]
    vectors = _validated_vectors(active_encoder(texts), len(texts))
    scores = vectors[1:] @ vectors[0]

    ranked = [
        {**citation, "score": float(score)}
        for citation, score in zip(citations, scores, strict=True)
    ]
    ranked.sort(key=lambda item: item["score"], reverse=True)
    return ranked[:limit]
