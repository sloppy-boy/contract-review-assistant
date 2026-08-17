"""统计 dev/test 植入组的缺陷覆盖分布。"""
import json, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(__file__).resolve().parent.parent  # S9：绝对路径（与 config.ROOT 一致）
for split in ("dev", "test"):
    c = Counter()
    n = 0
    for lp in sorted((ROOT / "eval/dataset" / split / "labels").glob("*.json")):
        l = json.loads(lp.read_text(encoding="utf-8"))
        if l["group"] != "implant":
            continue
        for d in l["defects"]:
            c[d["defectId"]] += 1
            n += 1
    print(f"{split}: {n} 个缺陷，{len(c)} 种")
    print("  ", dict(sorted(c.items())))
