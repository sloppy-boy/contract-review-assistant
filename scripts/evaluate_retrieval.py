"""Run the offline retrieval gate against precomputed ranked results.

Input JSON format::

    [{"query": "付款期限", "relevant": ["article-1"],
      "results": [{"id": "article-1"}, {"id": "article-9"}]}]

No contract text is required. The script exits 1 when a threshold is missed.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.evaluation_gate import RetrievalEvaluationGate, evaluate_retrieval


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("cases", type=Path, help="JSON file containing query/relevant/results cases")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--thresholds", default='{"recallAtK":0.8,"mrr":0.6,"citationAccuracy":0.9}')
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))
    if not isinstance(cases, list):
        raise SystemExit("cases must be a JSON array")
    metrics = evaluate_retrieval(cases, lambda query, k: next((case.get("results", []) for case in cases if case.get("query") == query), []), k=args.k)
    result = RetrievalEvaluationGate(json.loads(args.thresholds)).evaluate(metrics)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
