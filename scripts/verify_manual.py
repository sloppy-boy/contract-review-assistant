"""法条手册核验脚本（SPEC 2.5：语料版本正确性需核验，与官方版对比）。

用法：
  python scripts/verify_manual.py            # 打印核验清单（人工比对官方来源）
  python scripts/verify_manual.py --mark     # 人工确认后标记 verified=true
  python scripts/verify_manual.py --check    # 检查：全库禁止出现旧合同法、空条文、无版本
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.legal.manual import load_manual, load_manual_meta  # noqa: E402

MANUAL_PATH = Path("app/legal/manual_data.json")

FORBIDDEN = ["合同法（1999", "《中华人民共和国合同法》", "1999 年合同法", "旧合同法"]


def check() -> list[str]:
    """硬检查：禁旧合同法 / 无空条文 / 必须有版本与条文号（只检查条文内容，_meta 说明不算）。"""
    problems: list[str] = []
    articles = load_manual()
    if not articles:
        problems.append("手册为空")
    for a in articles:
        if not a.text.strip():
            problems.append(f"{a.id} 条文原文为空")
        if not a.version:
            problems.append(f"{a.id} 缺少版本")
        if not a.article:
            problems.append(f"{a.id} 缺少条文号")
        if len(a.text) < 10:
            problems.append(f"{a.id} 原文过短（疑似占位）")
        for bad in FORBIDDEN:
            if bad in a.text or bad in a.version or bad in a.article:
                problems.append(f"{a.id} 条文含旧合同法字样：{bad}")
                break
    return problems


def _read_raw() -> list[str]:
    return MANUAL_PATH.read_text(encoding="utf-8").splitlines()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="硬检查")
    ap.add_argument("--mark", action="store_true", help="标记已核验")
    args = ap.parse_args()

    if args.check:
        problems = check()
        if problems:
            print("❌ 检查未通过：")
            for p in problems:
                print(f"  - {p}")
            sys.exit(1)
        print("✅ 硬检查通过：无旧合同法、无空条文、均有版本/条文号")
        return

    articles = load_manual()
    meta = load_manual_meta()
    print(f"法条手册：{len(articles)} 条 | verified={meta.get('verified')}")
    print(f"核验指引：对照官方来源逐条比对条文号与原文——\n"
          f"  民法典：国家法律法规数据库 flk.npc.gov.cn；民诉法/反法：同上；个保法/数安法：同上\n")
    for a in articles:
        print(f"  [{a.id}] {a.version} · {a.article}（{len(a.text)}字）")

    if args.mark:
        data = json.loads(MANUAL_PATH.read_text(encoding="utf-8"))
        data["_meta"]["verified"] = True
        data["_meta"]["last_verified"] = "人工核验（对照官方来源）"
        MANUAL_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print("已标记 verified=true")


if __name__ == "__main__":
    main()
