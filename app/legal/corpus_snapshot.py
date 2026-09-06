"""法条语料快照：将报告与其生成时使用的法条原文绑定。"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Iterable

from pydantic import BaseModel


class LegalCorpusSnapshot(BaseModel):
    version: str
    contentHash: str
    articleCount: int
    verified: bool = False
    sources: list[str] = []

    @classmethod
    def create(cls, articles: Iterable[Any], *, metadata: dict[str, Any] | None = None) -> "LegalCorpusSnapshot":
        normalized = []
        sources: set[str] = set()
        for item in articles:
            raw = item.model_dump() if hasattr(item, "model_dump") else dict(item)
            normalized.append({key: raw.get(key, "") for key in ("id", "source", "version", "article", "text")})
            if raw.get("source"):
                sources.add(str(raw["source"]))
        normalized.sort(key=lambda article: article["id"])
        canonical = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        meta = metadata or {}
        return cls(
            version=meta.get("last_verified") or f"manual-{digest[:12]}",
            contentHash=digest,
            articleCount=len(normalized),
            verified=bool(meta.get("verified")),
            sources=sorted(sources),
        )

    @classmethod
    def from_manual(cls) -> "LegalCorpusSnapshot":
        from .manual import load_manual, load_manual_meta

        return cls.create(load_manual(), metadata=load_manual_meta())
