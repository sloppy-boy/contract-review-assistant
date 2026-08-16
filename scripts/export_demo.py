"""导出 3 份演出合同的真实报告为前端离线缓存（SPEC 2.8：缓存必须是真实 pipeline
跑出后导出，严禁手工编写假报告；仅对演出合同生效，新上传合同必须走在线）。

用法：python scripts/export_demo.py [--review-mode C]
输出：frontend/public/reports/{demo_high,demo_clean,demo_boundary}.json
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import REPORT_CACHE_DIR, using_mock  # noqa: E402
from app.graph import run_pipeline  # noqa: E402

DEMO = Path("eval/dataset/demo")


def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "C"
    if using_mock():
        print("[warn] mock 模式导出无效（缓存必须是真实 pipeline 输出），请配置 key 后运行")
    REPORT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("demo_high", "demo_clean", "demo_boundary"):
        text = (DEMO / f"{name}.txt").read_text(encoding="utf-8")
        report = run_pipeline(text, contract_type="purchase", contract_name=name, review_mode=mode)
        p = REPORT_CACHE_DIR / f"{name}.json"
        p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  导出 {p.name}: {report['summary']['total']} 条风险 mock={report['meta']['mock']}")
    print(f"[done] 离线缓存已导出到 {REPORT_CACHE_DIR}")


if __name__ == "__main__":
    main()
