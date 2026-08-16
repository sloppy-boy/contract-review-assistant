"""法条手册加载（manual_data.json）。"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


class Article(BaseModel):
    """法条条目：{法条ID, 版本, 条文号, 原文} —— 可溯源的最小单元（SPEC 2.1/2.5）。"""
    id: str = Field(description="法条 ID，如 CIVIL-585 / CIVPROC-35")
    source: str = Field(description="法律名称")
    version: str = Field(description="版本（现行有效版本全称）")
    article: str = Field(description="条文号，如 第585条")
    text: str = Field(description="条文原文")
    keywords: list[str] = Field(default_factory=list, description="检索关键词")


def load_manual(path: str | Path | None = None) -> list[Article]:
    """加载精选手册。path 缺省用 config.MANUAL_JSON_PATH。"""
    from ..config import MANUAL_JSON_PATH

    p = Path(path) if path else MANUAL_JSON_PATH
    data = json.loads(p.read_text(encoding="utf-8"))
    return [Article(**a) for a in data["articles"]]


def load_manual_meta(path: str | Path | None = None) -> dict:
    from ..config import MANUAL_JSON_PATH

    p = Path(path) if path else MANUAL_JSON_PATH
    return json.loads(p.read_text(encoding="utf-8")).get("_meta", {})
