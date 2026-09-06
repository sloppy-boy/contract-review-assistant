"""可解释的法规词项混合召回与确定性重排。"""
from __future__ import annotations

import re

from .manual import Article, load_manual


class ScoredArticle:
    def __init__(self, article: Article, score: float):
        self.article = article
        self.score = score


class HybridRetriever:
    """现有节点使用的法规检索接口；无 embedding key 时确定性词项召回。"""
    def __init__(self, articles: list[Article] | None = None, embed_fn=None):
        self.articles = articles if articles is not None else load_manual()
        self.embed_fn = embed_fn

    def search(self, query: str, top_k: int = 3) -> list[ScoredArticle]:
        terms = _terms(query)
        scored = []
        for article in self.articles:
            text = f"{article.article} {article.text} {' '.join(article.keywords)}"
            score = sum(1 for term in terms if term in text)
            if score:
                scored.append(ScoredArticle(article, float(score)))
        return sorted(scored, key=lambda hit: (-hit.score, hit.article.id))[:top_k]


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
