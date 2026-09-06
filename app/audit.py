"""追加式审计日志；不保存合同正文或模型提示词。"""
from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from types import MappingProxyType


class AuditStore:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, subject TEXT NOT NULL,
                action TEXT NOT NULL, resource_id TEXT NOT NULL, created_at TEXT NOT NULL)""")

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def record(self, *, tenant_id: str, subject: str, action: str, resource_id: str) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO audit_events(tenant_id, subject, action, resource_id, created_at) VALUES (?, ?, ?, ?, ?)",
                         (tenant_id, subject, action, resource_id, datetime.now(UTC).isoformat()))

    def list_events(self, tenant_id: str) -> list[MappingProxyType]:
        with self._connect() as conn:
            rows = conn.execute("SELECT subject, action, resource_id, created_at FROM audit_events WHERE tenant_id = ? ORDER BY id", (tenant_id,)).fetchall()
        return [MappingProxyType({"subject": r["subject"], "action": r["action"], "resourceId": r["resource_id"], "createdAt": r["created_at"]}) for r in rows]
