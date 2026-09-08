"""人工反馈；导出时只保留裁决标签，不含合同原文。"""
from __future__ import annotations

import sqlite3
from pathlib import Path


class FeedbackStore:
    def __init__(self, path: Path | str):
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS feedback (tenant_id TEXT, run_id TEXT, finding_id TEXT, decision TEXT, reason TEXT)")

    def _connect(self): return sqlite3.connect(self.path)

    def record(self, *, tenant_id: str, run_id: str, finding_id: str, decision: str, reason: str, contract_excerpt: str = "") -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO feedback VALUES (?, ?, ?, ?, ?)", (tenant_id, run_id, finding_id, decision, reason))

    def export_training_examples(self, tenant_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT run_id, finding_id, decision, reason FROM feedback WHERE tenant_id = ?", (tenant_id,)).fetchall()
        return [{"runId": r[0], "findingId": r[1], "decision": r[2], "reason": r[3]} for r in rows]

    def delete_run(self, *, tenant_id: str, run_id: str) -> int:
        """Delete feedback metadata linked to a purged review run."""
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM feedback WHERE tenant_id=? AND run_id=?", (tenant_id, run_id))
            return cursor.rowcount
