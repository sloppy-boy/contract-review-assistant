"""真实 LLM 模式冒烟（需 .env 已配置 key）：跑 3 份演出合同，输出摘要与成本。

用法：python scripts/smoke_real.py
"""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import DEMO_CONTRACT_DIR, using_mock  # noqa: E402
from app.graph import run_pipeline  # noqa: E402

if using_mock():
    print("[error] 未检测到 DEEPSEEK_API_KEY，无法跑真实模式")
    sys.exit(1)


def main() -> None:
    t0 = time.time()
    for name in ("demo_high", "demo_clean", "demo_boundary"):
        text = (DEMO_CONTRACT_DIR / f"{name}.txt").read_text(encoding="utf-8")
        t1 = time.time()
        report = run_pipeline(text, contract_type="purchase", contract_name=name, review_mode="C")
        dt = time.time() - t1
        s = report["summary"]
        m = report["meta"]
        print(f"[{name}] {s['high']}高/{s['medium']}中/{s['low']}低 总{s['total']} 耗时{dt:.1f}s "
              f"成本{m.get('costYuan')}元 tokens={m.get('tokens')} mock={m.get('mock')}")
        for r in report["risks"][:4]:
            print(f"    - [{r['severity']}] {r['clauseId']} {r['riskType']} "
                  f"(basis={r['legalBasis']['tier']}/{r['legalBasis']['articleId']}) "
                  f"status={r['status']}")
    print(f"\n总计 {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
