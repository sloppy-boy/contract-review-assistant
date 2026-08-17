"""共享文本正则（S13 去重）：条款号 / 关键数值 / 百分比 / 天数 / 金额。

extract.py 与 rule_checker.py 此前各自重复定义同一批正则（口径漂移风险），
统一收敛到本模块，两处 import 同一份定义。
"""
from __future__ import annotations

import re

# 条款号："第X条"（中文数字或阿拉伯数字）
CLAUSE_TOP_RE = re.compile(r"(?m)^\s*(第[0-9一二三四五六七八九十百千零两]+条)")
# 子编号："1." / "1.2、" 等
SUB_NUM_RE = re.compile(r"(?m)^\s*(\d+(?:\.\d+)*)\s*[、.．]")
# 条款引用："第X条"（行内）
REF_RE = re.compile(r"第\s*([0-9一二三四五六七八九十百千零两]+)\s*条")
# 金额："1000 元" / "200 万元" / "1.5 亿元"
AMOUNT_RE = re.compile(r"([\d,]+(?:\.\d+)?)\s*(?:万|亿)?\s*元")
# 天数："7 日" / "5 个工作日" / "30 天"
DAYS_RE = re.compile(r"(\d+)\s*(?:个)?(?:工作日|天|日)")
# 百分比："30%" / "30.5％"
PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[%％]")
