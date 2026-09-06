"""持久化审查运行记录。

LangGraph 黑板只服务一次推理；本模块保存调用方需要恢复和审计的运行生命周期。
SQLite 是默认 Adapter，调用方只依赖 ReviewRunStore 的小接口，后续可替换为 Postgres。
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class ReviewRunStore:
    """审查任务和不可变生命周期事件的 SQLite Adapter。"""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS review_runs (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL DEFAULT 'local',
                    contract_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage INTEGER NOT NULL DEFAULT 0,
                    stage_status TEXT NOT NULL DEFAULT 'idle',
                    stage_detail TEXT NOT NULL DEFAULT '',
                    stage_times_json TEXT NOT NULL DEFAULT '[0, 0, 0, 0]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    report_json TEXT,
                    error TEXT,
                    balance_exhausted INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS review_run_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    type TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES review_runs(id)
                );
                CREATE INDEX IF NOT EXISTS idx_review_run_events_run_id
                ON review_run_events(run_id, id);
                CREATE TABLE IF NOT EXISTS finding_dispositions (
                    run_id TEXT NOT NULL,
                    finding_id TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, finding_id),
                    FOREIGN KEY(run_id) REFERENCES review_runs(id)
                );
                """
            )
            columns = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(review_runs)").fetchall()
            }
            if "balance_exhausted" not in columns:
                conn.execute(
                    "ALTER TABLE review_runs ADD COLUMN balance_exhausted INTEGER NOT NULL DEFAULT 0"
                )
            if "tenant_id" not in columns:
                conn.execute("ALTER TABLE review_runs ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'local'")

    @staticmethod
    def _run_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        data["stageTimes"] = json.loads(data.pop("stage_times_json"))
        data["tenantId"] = data.pop("tenant_id")
        data["stageDetail"] = data.pop("stage_detail")
        data["stageStatus"] = data.pop("stage_status")
        data["createdAt"] = data.pop("created_at")
        data["updatedAt"] = data.pop("updated_at")
        data["balanceExhausted"] = bool(data.pop("balance_exhausted"))
        report = data.pop("report_json")
        if report is not None:
            data["report"] = json.loads(report)
        return data

    def _append_event(self, conn: sqlite3.Connection, run_id: str, kind: str, data: dict[str, Any]) -> None:
        conn.execute(
            "INSERT INTO review_run_events(run_id, type, data_json, created_at) VALUES (?, ?, ?, ?)",
            (run_id, kind, json.dumps(data, ensure_ascii=False), _utc_now()),
        )

    def create_run(self, *, contract_type: str, tenant_id: str = "local") -> dict[str, Any]:
        run_id = uuid.uuid4().hex
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO review_runs
                   (id, tenant_id, contract_type, status, created_at, updated_at)
                   VALUES (?, ?, ?, 'queued', ?, ?)""",
                (run_id, tenant_id, contract_type, now, now),
            )
            self._append_event(conn, run_id, "created", {"status": "queued", "contractType": contract_type, "tenantId": tenant_id})
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM review_runs WHERE id = ?", (run_id,)).fetchone()
        return self._run_dict(row) if row else None

    def list_runs(self, *, tenant_id: str, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """按租户返回最新审查历史；列表不返回大体积报告正文。"""
        query = "SELECT * FROM review_runs WHERE tenant_id = ?"
        params: list[Any] = [tenant_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(max(1, min(limit, 200)))
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        runs = []
        for row in rows:
            run = self._run_dict(row)
            report = run.pop("report", None)
            runs.append({
                **run,
                "summary": (report or {}).get("summary"),
                "contract": (report or {}).get("contract"),
            })
        return runs

    def update_progress(self, run_id: str, *, stage: int, status: str, detail: str = "", stage_times: list[int] | None = None) -> dict[str, Any]:
        current = self.get_run(run_id)
        if current is None:
            raise KeyError(f"review run not found: {run_id}")
        times = stage_times if stage_times is not None else current["stageTimes"]
        now = _utc_now()
        event = {"stage": stage, "stageStatus": status, "stageDetail": detail, "stageTimes": times}
        with self._connect() as conn:
            conn.execute(
                """UPDATE review_runs
                   SET status = 'running', stage = ?, stage_status = ?, stage_detail = ?,
                       stage_times_json = ?, updated_at = ?
                   WHERE id = ?""",
                (stage, status, detail, json.dumps(times), now, run_id),
            )
            self._append_event(conn, run_id, "progress", event)
        return self.get_run(run_id)  # type: ignore[return-value]

    def list_events(self, run_id: str, *, after_id: int = 0) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, type, data_json, created_at FROM review_run_events WHERE run_id = ? AND id > ? ORDER BY id",
                (run_id, after_id),
            ).fetchall()
        return [
            {"id": row["id"], "type": row["type"], "data": json.loads(row["data_json"]), "createdAt": row["created_at"]}
            for row in rows
        ]

    def complete_run(self, run_id: str, report: dict[str, Any], *, elapsed_ms: int, stage_times: list[int]) -> dict[str, Any]:
        # 报告离开任务详情页后仍应携带完整阶段耗时，供导出与报告页面展示。
        report = {**report, "meta": {**report.get("meta", {}), "stageTimes": stage_times}}
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """UPDATE review_runs
                   SET status = 'done', stage = 3, stage_status = 'done', stage_detail = '',
                       stage_times_json = ?, report_json = ?, updated_at = ?
                   WHERE id = ?""",
                (json.dumps(stage_times), json.dumps(report, ensure_ascii=False), now, run_id),
            )
            self._append_event(conn, run_id, "completed", {"elapsedMs": elapsed_ms, "stageTimes": stage_times})
        return self.get_run(run_id)  # type: ignore[return-value]

    def fail_run(self, run_id: str, error: str, *, balance_exhausted: bool = False) -> dict[str, Any]:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """UPDATE review_runs
                   SET status = 'failed', error = ?, balance_exhausted = ?, updated_at = ?
                   WHERE id = ?""",
                (error, int(balance_exhausted), now, run_id),
            )
            self._append_event(conn, run_id, "failed", {"error": error, "balanceExhausted": balance_exhausted})
        return self.get_run(run_id)  # type: ignore[return-value]

    def set_disposition(self, run_id: str, finding_id: str, decision: str, reason: str) -> dict[str, Any]:
        valid = {"accepted", "accepted_with_changes", "rejected", "escalated"}
        if decision not in valid:
            raise ValueError("invalid disposition")
        if decision in {"accepted_with_changes", "rejected", "escalated"} and not reason.strip():
            raise ValueError("reason is required for this disposition")
        if self.get_run(run_id) is None:
            raise KeyError(f"review run not found: {run_id}")
        now = _utc_now()
        result = {"decision": decision, "reason": reason.strip(), "updatedAt": now}
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO finding_dispositions(run_id, finding_id, decision, reason, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(run_id, finding_id) DO UPDATE SET
                   decision = excluded.decision, reason = excluded.reason, updated_at = excluded.updated_at""",
                (run_id, finding_id, decision, result["reason"], now),
            )
            self._append_event(conn, run_id, "disposition", {"findingId": finding_id, **result})
        return result

    def get_dispositions(self, run_id: str) -> dict[str, dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT finding_id, decision, reason, updated_at FROM finding_dispositions WHERE run_id = ?",
                (run_id,),
            ).fetchall()
        return {
            row["finding_id"]: {"decision": row["decision"], "reason": row["reason"], "updatedAt": row["updated_at"]}
            for row in rows
        }
