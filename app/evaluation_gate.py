"""发布前评测门禁。"""
from __future__ import annotations

from collections.abc import Callable, Iterable
import math


class EvaluationGate:
    def __init__(self, thresholds: dict[str, float]): self.thresholds = thresholds

    def evaluate(self, metrics: dict[str, float]) -> dict:
        failures = [name for name, minimum in self.thresholds.items() if (metrics.get(name, 0) < minimum if name != "falsePositiveRate" else metrics.get(name, 1) > minimum)]
        return {"passed": not failures, "failures": failures, "metrics": metrics, "thresholds": self.thresholds}


def evaluate_retrieval(cases: Iterable[dict], search: Callable[[str, int], list], *, k: int = 5) -> dict[str, float | int]:
    """Evaluate ranked citations without reading contract bodies into metrics.

    Each case contains ``query`` and a non-empty ``relevant`` list of IDs.
    Recall is averaged per query, MRR uses the first relevant result, and
    citation accuracy is relevant returned IDs divided by all returned IDs.
    """
    if isinstance(k, bool) or not isinstance(k, int) or not 1 <= k <= 100:
        raise ValueError("k must be between 1 and 100")
    rows = list(cases)
    if not rows:
        return {"queries": 0, "recallAtK": 0.0, "mrr": 0.0, "citationAccuracy": 0.0}
    recalls, reciprocal_ranks, relevant_returned, total_returned = [], [], 0, 0
    for case in rows:
        query, relevant = case.get("query"), set(case.get("relevant", []))
        if not isinstance(query, str) or not query.strip() or not relevant:
            raise ValueError("each retrieval case needs query and relevant IDs")
        hits = list(search(query, k))[:k]
        ids = [item.get("id") if isinstance(item, dict) else getattr(item, "id", None) for item in hits]
        matched = [item_id for item_id in ids if item_id in relevant]
        recalls.append(len(set(matched)) / len(relevant))
        reciprocal_ranks.append(1 / (next((index + 1 for index, item_id in enumerate(ids) if item_id in relevant), 0) or math.inf))
        relevant_returned += len(matched)
        total_returned += len(ids)
    return {"queries": len(rows), "recallAtK": round(sum(recalls) / len(recalls), 3), "mrr": round(sum(reciprocal_ranks) / len(reciprocal_ranks), 3), "citationAccuracy": round(relevant_returned / total_returned, 3) if total_returned else 0.0}


class RetrievalEvaluationGate(EvaluationGate):
    """Named gate for Recall@K, MRR and citation correctness thresholds."""

    def evaluate(self, metrics: dict[str, float]) -> dict:
        normalized = {name: float(value) for name, value in metrics.items() if isinstance(value, (int, float)) and math.isfinite(value)}
        return super().evaluate(normalized)
