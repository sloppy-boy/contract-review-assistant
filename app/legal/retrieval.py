"""法条混合检索（SPEC 2.5）：关键词（BM25 简化）+ 向量（硅基流动 bge-m3）双路。

- 嵌入是硬依赖（DeepSeek 无 embedding API）：bge-m3 经 SiliconFlow embeddings API 调用；
  无 SILICONFLOW_API_KEY 时自动降级为纯关键词路（链路不崩，数字有效性受限）。
- 检索结果带 {法条ID, 版本, 条文号, 原文} 一路带到报告 = 可溯源。
- 嵌入结果本地缓存（.cache/embed_cache.json，gitignore），避免重复计费。
"""
from __future__ import annotations

import json
import math
import re
import threading
from collections import Counter

import httpx

from ..config import (
    EMBED_CACHE_PATH,
    EMBED_MODEL,
    SILICONFLOW_API_KEY,
    SILICONFLOW_BASE_URL,
    TOP_K_ARTICLES,
)
from .manual import Article, load_manual

_NUM_RE = re.compile(r"\d+(?:\.\d+)?(?:%|％)?")
_CLAUSE_REF_RE = re.compile(r"(?:第\s*)?(\d+(?:\.\d+)*)\s*条")

# 嵌入缓存读写锁（13 个 worker 并行检索时会并发读改写缓存，必须串行化）
_embed_lock = threading.Lock()


class ScoredArticle:
    __slots__ = ("article", "score")

    def __init__(self, article: Article, score: float):
        self.article = article
        self.score = score


class HybridRetriever:
    """混合检索器。embed_fn 可注入（测试用）；缺省走 SiliconFlow bge-m3。"""

    def __init__(self, articles: list[Article] | None = None, embed_fn=None):
        self.articles = articles if articles is not None else load_manual()
        self.embed_fn = embed_fn  # 测试注入用；None 时走真实 bge-m3（_embed_texts）
        self._doc_vectors: list[list[float]] | None = None
        # 双重检查锁：13 个 worker 首次并行检索时只做一次法条批量嵌入（防重复 API 调用）
        self._doc_vectors_lock = threading.Lock()
        # 关键词词典（条文关键词 + 条文原文高频词）用于轻量中文切词（无 jieba 依赖）
        self._vocab: set[str] = set()
        for a in self.articles:
            self._vocab.update(a.keywords)
            self._vocab.update(_extract_terms(a.text))
        self._doc_tokens = [Counter(_tokenize(a.text, self._vocab)) for a in self.articles]
        self._idf = self._build_idf()

    # ------------------------------------------------------------------ 关键词路
    def _build_idf(self) -> dict[str, float]:
        n = len(self.articles)
        df: Counter = Counter()
        for c in self._doc_tokens:
            df.update(c.keys())
        return {w: math.log((n + 1) / (c + 1)) + 1 for w, c in df.items()}

    def keyword_scores(self, query: str) -> list[float]:
        qt = _tokenize(query, self._vocab)
        qc = Counter(qt)
        scores = []
        for doc in self._doc_tokens:
            s = 0.0
            for w, qf in qc.items():
                tf = doc.get(w, 0)
                if tf:
                    s += qf * (1 + math.log(tf)) * self._idf.get(w, 1.0)
            scores.append(s)
        return scores

    # ------------------------------------------------------------------ 向量路
    def _embed_texts(self, texts: list[str]) -> list[list[float]] | None:
        """批量嵌入（SiliconFlow bge-m3），带本地缓存 + 线程锁 + 原子写。失败返回 None 降级。"""
        if not SILICONFLOW_API_KEY:
            return None
        with _embed_lock:
            cache: dict = {}
            if EMBED_CACHE_PATH.exists():
                try:
                    cache = json.loads(EMBED_CACHE_PATH.read_text(encoding="utf-8"))
                except Exception:
                    cache = {}
            missing = [t for t in texts if t not in cache]
            if missing:
                try:
                    with httpx.Client(timeout=120) as client:
                        resp = client.post(
                            f"{SILICONFLOW_BASE_URL}/embeddings",
                            headers={
                                "Authorization": f"Bearer {SILICONFLOW_API_KEY}",
                                "Content-Type": "application/json",
                            },
                            json={"model": EMBED_MODEL, "input": missing},
                        )
                        resp.raise_for_status()
                        data = resp.json()["data"]
                        for item in data:
                            cache[missing[item["index"]]] = item["embedding"]
                except Exception:
                    return None  # 嵌入失败 → 纯关键词路降级
                # 原子写（先写临时文件再替换，防并发/中断损坏）
                EMBED_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
                tmp = EMBED_CACHE_PATH.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
                tmp.replace(EMBED_CACHE_PATH)
            return [cache[t] for t in texts]

    def vector_scores(self, query: str) -> list[float] | None:
        if self.embed_fn is not None:  # 测试注入路径
            qv = self.embed_fn(query)
            if qv is None:
                return None
            if self._doc_vectors is None:
                self._doc_vectors = [
                    v for v in (self.embed_fn(a.text) for a in self.articles)
                ]
            return [_cosine(qv, d) for d in self._doc_vectors]
        qv = self._embed_texts([query])
        if qv is None:
            return None
        if self._doc_vectors is None:
            with self._doc_vectors_lock:
                if self._doc_vectors is None:  # 双重检查：只嵌入一次
                    self._doc_vectors = self._embed_texts([a.text for a in self.articles])
        if self._doc_vectors is None:
            return None
        return [_cosine(qv[0], d) for d in self._doc_vectors]

    # ------------------------------------------------------------------ 融合
    def search(self, query: str, top_k: int = TOP_K_ARTICLES) -> list[ScoredArticle]:
        kw = self.keyword_scores(query)
        max_kw = max(kw) if kw and max(kw) > 0 else 1.0
        kw_n = [s / max_kw for s in kw]
        vec = self.vector_scores(query)
        if vec is not None:
            max_v = max(vec) if vec and max(vec) > 0 else 1.0
            vec_n = [s / max_v for s in vec]
            fused = [0.4 * k + 0.6 * v for k, v in zip(kw_n, vec_n)]
        else:
            fused = kw_n  # 无向量 → 纯关键词路
        ranked = sorted(
            range(len(self.articles)), key=lambda i: fused[i], reverse=True
        )
        # 过滤零分
        out = [
            ScoredArticle(self.articles[i], fused[i])
            for i in ranked
            if fused[i] > 0
        ]
        return out[:top_k]


def _extract_terms(text: str) -> list[str]:
    """从条文原文提取 2-4 字术语（用于词典构建，中文无空格分词兜底）。"""
    terms = set()
    for m in re.finditer(r"[\u4e00-\u9fff]{2,4}", text):
        terms.add(m.group())
    return list(terms)


def _tokenize(text: str, vocab: set[str]) -> list[str]:
    """轻量切词：数值模式 + 词典最长匹配 + 2-gram 兜底。"""
    toks: list[str] = list(_NUM_RE.findall(text))
    toks += list(_CLAUSE_REF_RE.findall(text))
    rest = _NUM_RE.sub(" ", text)
    for w in sorted(vocab, key=len, reverse=True):
        if len(w) >= 2 and w in rest:
            toks.append(w)
    # 2-gram 兜底（未命中词典的连续汉字）
    for i in range(len(rest) - 1):
        pair = rest[i : i + 2]
        if re.fullmatch(r"[\u4e00-\u9fff]{2}", pair):
            toks.append(pair)
    return toks


def _cosine(a: list[float], b: list[float]) -> float:
    import numpy as np

    na, nb = np.array(a), np.array(b)
    denom = float(np.linalg.norm(na) * np.linalg.norm(nb))
    if denom == 0:
        return 0.0
    return float(np.dot(na, nb) / denom)
