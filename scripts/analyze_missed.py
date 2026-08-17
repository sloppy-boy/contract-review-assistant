"""分析：首轮 C 档报告的漏检/误报明细（调优依据）。"""
import json, sys
from collections import Counter
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from eval.score import normalize_clause_id

ROOT = Path(__file__).resolve().parent.parent  # S9：绝对路径（与 config.ROOT 一致）
labels_dir = ROOT / "eval/dataset/dev/labels"
reports_dir = ROOT / "eval/output/C/dev"

missed = Counter()
hit_ids = []
for lp in sorted(labels_dir.glob("*.json")):
    label = json.loads(lp.read_text(encoding="utf-8"))
    if label["group"] != "implant":
        continue
    cid = label["contractId"]
    rp = reports_dir / f"{cid}.json"
    if not rp.exists():
        print(f"[missing report] {cid}")
        continue
    report = json.loads(rp.read_text(encoding="utf-8"))
    risks = report.get("risks", [])
    matched = set()
    for d in label["defects"]:
        for i, r in enumerate(risks):
            if i in matched:
                continue
            if (normalize_clause_id(d["clauseId"]) == normalize_clause_id(r["clauseId"])
                    and d["riskType"] == r["riskType"] and d["severity"] == r["severity"]):
                matched.add(i)
                hit_ids.append(d["defectId"])
                break
        else:
            missed[d["defectId"]] += 1
print("漏检缺陷分布（defectId: 次数）：")
for k, v in missed.most_common():
    print(f"  {k}: {v}")
print("命中:", Counter(hit_ids))
