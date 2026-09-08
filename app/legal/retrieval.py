"""可解释的法规词项混合召回与确定性重排。"""
from __future__ import annotations

import re
import math

from .manual import Article, load_manual


class ScoredArticle:
    def __init__(self, article: Article, score: float, metadata: dict | None = None):
        self.article = article
        self.score = score
        self.metadata = metadata or {}


class HybridRetriever:
    """Keyword + optional dense recall with a deterministic explainable rerank."""
    def __init__(self, articles: list[Article] | None = None, embed_fn=None, *, lexical_weight: float = 0.5, dense_weight: float = 0.5):
        self.articles = articles if articles is not None else load_manual()
        self.embed_fn = embed_fn
        if lexical_weight < 0 or dense_weight < 0 or lexical_weight + dense_weight <= 0:
            raise ValueError("retrieval weights must be non-negative and non-zero")
        self.lexical_weight, self.dense_weight = float(lexical_weight), float(dense_weight)

    @staticmethod
    def _text(article) -> str:
        if isinstance(article, dict):
            return f"{article.get('article', '')} {article.get('text', '')} {' '.join(article.get('keywords', []))}"
        return f"{article.article} {article.text} {' '.join(article.keywords)}"

    @staticmethod
    def _cosine(left, right) -> float:
        try:
            a, b = list(left), list(right)
            if not a or len(a) != len(b):
                return 0.0
            dot = sum(float(x) * float(y) for x, y in zip(a, b))
            norm = math.sqrt(sum(float(x) ** 2 for x in a) * sum(float(y) ** 2 for y in b))
            return max(0.0, min(1.0, dot / norm)) if norm else 0.0
        except (TypeError, ValueError, OverflowError):
            return 0.0

    def search(self, query: str, top_k: int = 3) -> list[ScoredArticle]:
        if not isinstance(query, str) or not query.strip():
            return []
        if not isinstance(top_k, int) or not 1 <= top_k <= 100:
            raise ValueError("top_k must be between 1 and 100")
        terms = _terms(query)
        lexical = []
        for article in self.articles:
            text = self._text(article)
            score = sum(1 for term in terms if term in text)
            lexical.append((article, float(score)))
        max_lexical = max((score for _, score in lexical), default=0.0)
        dense_query = None
        if self.embed_fn is not None:
            try:
                dense_query = self.embed_fn(query)
            except Exception:
                dense_query = None
        scored = []
        for article, raw_lexical in lexical:
            lexical_score = raw_lexical / max_lexical if max_lexical else 0.0
            dense_score = self._cosine(dense_query, self.embed_fn(self._text(article))) if dense_query is not None else 0.0
            if raw_lexical == 0 and dense_score == 0:
                continue
            if dense_query is None:
                combined = lexical_score
                strategy = "lexical_hybrid_rerank"
            else:
                total = self.lexical_weight + self.dense_weight
                combined = (self.lexical_weight * lexical_score + self.dense_weight * dense_score) / total
                strategy = "dense_lexical_rerank"
            article_id = article.get("id", "") if isinstance(article, dict) else article.id
            scored.append(ScoredArticle(article, combined, {"strategy": strategy, "lexicalScore": round(lexical_score, 6), "denseScore": round(dense_score, 6), "matchedTerms": sorted(term for term in terms if term in self._text(article))}))
        return sorted(scored, key=lambda hit: (-hit.score, (hit.article.get("id", "") if isinstance(hit.article, dict) else hit.article.id)))[:top_k]


def _terms(text: str) -> set[str]:
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    return set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_-]+", text)) | {
        chinese[index:index + 2] for index in range(max(0, len(chinese) - 1))
    }


class LegalRetriever:
    def __init__(self, articles: list[dict]): self.articles = articles

    def search(self, query: str, *, version: str | None = None, limit: int = 3) -> list[dict]:
        # 中文没有空格分词；用二元词片段补足完整短语无法直接命中的情形，
        # 同时保留原始词段以便英文/数字法规编号查询。
        tokens = _terms(query)
        scored = []
        for article in self.articles:
            if version and article.get("version") != version: continue
            text = article.get("text", "")
            score = sum(1 for token in tokens if token in text)
            if score:
                scored.append((score, article))
        scored.sort(key=lambda item: (-item[0], item[1].get("id", "")))
        return [{**article, "retrieval": {"strategy": "lexical_hybrid_rerank", "score": score, "matchedTerms": sorted(t for t in tokens if t in article.get("text", ""))}} for score, article in scored[:limit]]
