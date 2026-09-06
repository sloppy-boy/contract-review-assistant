"""低敏感度流水线 trace 记录与聚合。"""
from __future__ import annotations

import sqlite3
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

    def summary(self, tenant_id: str) -> dict:
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) runs, SUM(error IS NOT NULL) errors, AVG(elapsed_ms) latency, SUM(input_tokens) input_tokens, SUM(output_tokens) output_tokens FROM traces WHERE tenant_id = ?", (tenant_id,)).fetchone()
        return {"runs": row["runs"], "errors": row["errors"] or 0, "avgLatencyMs": round(row["latency"] or 0), "inputTokens": row["input_tokens"] or 0, "outputTokens": row["output_tokens"] or 0}
