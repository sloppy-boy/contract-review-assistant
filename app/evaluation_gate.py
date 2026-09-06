"""发布前评测门禁。"""
from __future__ import annotations


class EvaluationGate:
    def __init__(self, thresholds: dict[str, float]): self.thresholds = thresholds

    def evaluate(self, metrics: dict[str, float]) -> dict:
        failures = [name for name, minimum in self.thresholds.items() if (metrics.get(name, 0) < minimum if name != "falsePositiveRate" else metrics.get(name, 1) > minimum)]
        return {"passed": not failures, "failures": failures, "metrics": metrics, "thresholds": self.thresholds}
