"""导出前端评测页数据（frontend/public/eval-results.json）：
从 eval/output 聚合 dev/test 两个 split 的 A/B/C/baseline 三档指标 + 及格线，
前端 EvalBoard 消费（test 为 held-out 最终汇报口径）。

用法：python scripts/export_eval_summary.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import BEAT_BASELINE_POINTS  # noqa: E402
from eval import score as sc  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _export_split(split: str) -> dict:
    dataset = ROOT / "eval" / "dataset"
    split_dir = dataset / split
    labels, _contracts = sc.load_split(split_dir)
    reports_root = ROOT / "eval" / "output"
    modes = ["A", "B", "C", "baseline"]
    out: dict = {"split": split}
    for mode in modes:
        reports = sc.load_reports(reports_root, mode, split)
        implants = [l for l in labels if l["group"] == "implant"]
        cleans = [l for l in labels if l["group"] == "clean"]
        boundaries = [l for l in labels if l["group"] == "boundary"]
        agg = sc.aggregate([sc.score_contract(l, reports.get(l["contractId"], {"risks": []})) for l in implants])
        fp = [sc.clean_fp_rate(reports.get(l["contractId"], {"risks": [], "clauses": []})) for l in cleans]
        agg["cleanFpRate"] = round(sum(fp) / len(fp), 3) if fp else None
        agg["boundary"] = sc.boundary_metrics(boundaries, reports)
        agg["reviewModule"] = sc.review_module_metrics(implants, reports)
        agg["costPerContract"] = sc._cost(reports)
        agg["latencyPerContract"] = sc._latency(reports)
        out[mode] = agg
    base = out.get("baseline", {})
    c = out.get("C", {})
    out["winBaseline"] = (c.get("recall", 0) - base.get("recall", 0) >= BEAT_BASELINE_POINTS / 100) or (
        c.get("f1", 0) - base.get("f1", 0) >= BEAT_BASELINE_POINTS / 100
    )
    out["pass"] = c.get("recall", 0) >= 0.85 and (c.get("cleanFpRate") or 1) <= 0.15 and out["winBaseline"]
    return out


def export() -> None:
    out = {"dev": _export_split("dev"), "test": _export_split("test")}
    dest = ROOT / "frontend" / "public" / "eval-results.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    for split, d in out.items():
        c = d["C"]
        print(f"[{split}] C 档：召回 {c.get('recall', 0):.1%} 误报 {c.get('cleanFpRate', 0):.1%} "
              f"赢基线={d['winBaseline']} pass={d['pass']}")
    print(f"[done] 已导出 {dest}")


if __name__ == "__main__":
    export()
