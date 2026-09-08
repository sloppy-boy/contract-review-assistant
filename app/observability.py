"""低敏感度流水线 trace 记录与聚合。"""
from __future__ import annotations

import sqlite3
import math
from pathlib import Path


class TraceStore:
    def __init__(self, path: Path | str):
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS traces (
                id INTEGER PRIMARY KEY, tenant_id TEXT NOT NULL, run_id TEXT NOT NULL, stage TEXT NOT NULL,
                elapsed_ms INTEGER NOT NULL, input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, error TEXT)""")

    def _connect(self):
        conn = sqlite3.connect(self.path); conn.row_factory = sqlite3.Row; return conn

    def record(self, *, tenant_id: str, run_id: str, stage: str, elapsed_ms: int, input_tokens: int = 0, output_tokens: int = 0, error: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO traces(tenant_id, run_id, stage, elapsed_ms, input_tokens, output_tokens, error) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (tenant_id, run_id, stage, elapsed_ms, input_tokens, output_tokens, error))

    def delete_run(self, *, tenant_id: str, run_id: str) -> int:
        """Delete low-sensitivity trace rows linked to a purged review run."""
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM traces WHERE tenant_id=? AND run_id=?", (tenant_id, run_id))
            return cursor.rowcount

    def summary(self, tenant_id: str) -> dict:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) runs, SUM(error IS NOT NULL) errors, AVG(elapsed_ms) latency, SUM(input_tokens) input_tokens, SUM(output_tokens) output_tokens FROM traces WHERE tenant_id = ?", (tenant_id,)).fetchone()
            latencies = [item[0] for item in conn.execute("SELECT elapsed_ms FROM traces WHERE tenant_id = ? ORDER BY elapsed_ms", (tenant_id,)).fetchall()]
        def percentile(fraction: float) -> int:
            if not latencies:
                return 0
            return int(latencies[max(0, min(len(latencies) - 1, math.ceil(fraction * len(latencies)) - 1))])
        errors = row["errors"] or 0
        runs = row["runs"] or 0
        p95 = percentile(0.95)
        error_rate = round(errors / runs, 3) if runs else 0.0
        slo_ok = p95 <= 1000 and error_rate <= 0.05
        return {
            "runs": runs, "errors": errors, "avgLatencyMs": round(row["latency"] or 0),
            "inputTokens": row["input_tokens"] or 0, "outputTokens": row["output_tokens"] or 0,
            "p50LatencyMs": percentile(0.50), "p95LatencyMs": p95, "errorRate": error_rate,
            "slo": {"latencyP95Ms": p95, "maxLatencyP95Ms": 1000, "errorRate": error_rate, "maxErrorRate": 0.05, "status": "ok" if slo_ok else "breached"},
        }
