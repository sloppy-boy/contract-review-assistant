"""评测运行器：对指定 split 的每份合同跑流水线（A/B/C 三档）+ 规则基线，报告落盘。

并发加速（多 Agent 并行评测）：
- --jobs N：多份合同并行跑（每份 run_pipeline 新建独立 graph/state/LLM 实例，
  天然隔离无串数据）；全局 LLM 信号量（LLM_MAX_CONCURRENT）自动限流防 429。
- 建议 jobs = 3~6（受 LLM_MAX_CONCURRENT 上限约束）。

用法：
  python scripts/run-eval.py --split dev                # mock 或真实，三档 + 基线
  python scripts/run-eval.py --split test --modes C,baseline   # held-out 最终汇报
  python scripts/run-eval.py --split dev --modes C --limit 10  # 快速子集（消融成本控制）
  python scripts/run-eval.py --split dev --modes C --jobs 4    # 并行评测加速

输出：eval/output/{mode}/{split}/{contractId}.json（score.py 消费）
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import using_mock  # noqa: E402
from app.graph import run_pipeline  # noqa: E402
from eval.rule_baseline import baseline_report  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
_print_lock = threading.Lock()  # 并行时进度打印不交错


def _run_one(cid: str, mode: str, split: str, contracts_dir: Path, out_root: Path) -> tuple[str, str, bool]:
    """跑单份合同的单个档位（线程池 worker）。返回 (cid, mode, ok)。"""
    try:
        text = (contracts_dir / f"{cid}.txt").read_text(encoding="utf-8")
        if mode == "baseline":
            report = baseline_report(text, contract_name=cid, contract_type="purchase")
        else:
            report = run_pipeline(text, contract_type="purchase", contract_name=cid, review_mode=mode)
        report.setdefault("meta", {})["contractId"] = cid
        p = out_root / mode / split / f"{cid}.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return cid, mode, True
    except Exception as e:
        with _print_lock:
            print(f"  ✗ {cid} [{mode}] 失败：{e}")
        return cid, mode, False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev", choices=["dev", "test"])
    ap.add_argument("--modes", default="A,B,C,baseline")
    ap.add_argument("--limit", type=int, default=0, help="只跑前 N 份（0=全部）")
    ap.add_argument("--jobs", type=int, default=1, help="并行合同数（1=串行；建议 3~6）")
    ap.add_argument("--output", default="eval/output")
    args = ap.parse_args()

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    contracts_dir = ROOT / "eval" / "dataset" / args.split / "contracts"
    out_root = ROOT / args.output
    cids = sorted(p.stem for p in contracts_dir.glob("*.txt"))
    if args.limit:
        cids = cids[: args.limit]

    print(f"[run-eval] split={args.split} 合同 {len(cids)} 份 档位 {modes} jobs={args.jobs} "
          f"{'(mock 规则模式，数字仅供链路验证)' if using_mock() else '(真实 LLM 模式)'}")
    tasks = [(cid, mode) for cid in cids for mode in modes]
    t0 = time.time()
    ok_count = 0
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = {
            pool.submit(_run_one, cid, mode, args.split, contracts_dir, out_root): (cid, mode)
            for cid, mode in tasks
        }
        done = 0
        for fut in as_completed(futures):
            done += 1
            cid, mode, ok = fut.result()
            if ok:
                ok_count += 1
            if done % 5 == 0 or done == len(futures):
                with _print_lock:
                    print(f"  进度 {done}/{len(futures)}（{cid} [{mode}]）")
    dt = time.time() - t0
    print(f"[done] 总耗时 {dt:.1f}s（{len(cids)} 份 × {len(modes)} 档，成功 {ok_count}/{len(tasks)}，"
          f"jobs={args.jobs}）。下一步：python eval/score.py --reports {args.output} --split {args.split}")


if __name__ == "__main__":
    main()
