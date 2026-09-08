"""Small durable SQLite task queue for local orchestration.

Only task metadata and caller supplied references are persisted. Callers must
never put contract text, credentials, or model output in the payload.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


_ACTIVE = {"queued", "retry_wait", "running"}
_TERMINAL = {"succeeded", "dead", "cancelled"}
_REFERENCE_KEYS = {"runId", "assetId", "requestId", "playbookId", "version"}


def _now(value: str | datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("time must be timezone-aware")
        return value.astimezone(UTC)
    raw = value.strip()
    if raw.endswith(("Z", "z")):
        raw = raw[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ValueError("time must be ISO datetime") from exc
    return _now(parsed)


def _iso(value: str | datetime | None = None) -> str:
    return _now(value).isoformat()


class TaskQueue:
    """SQLite-backed queue with compare-and-set claims and dead letters."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect(write=True) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL DEFAULT 'local',
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    available_at TEXT NOT NULL,
                    worker_id TEXT,
                    last_error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS tasks_ready
                    ON tasks(status, available_at, created_at);
                CREATE INDEX IF NOT EXISTS tasks_kind_status
                    ON tasks(kind, status, updated_at);
                """
            )
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
            if "tenant_id" not in columns:
                conn.execute("ALTER TABLE tasks ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'local'")

    @contextmanager
    def _connect(self, *, write: bool = False):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
            yield conn
            if write:
                conn.commit()
        except BaseException:
            if write:
                conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _task(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result["payload"] = json.loads(result.pop("payload_json"))
        result["tenantId"] = result.pop("tenant_id")
        result["maxAttempts"] = result.pop("max_attempts")
        result["availableAt"] = result.pop("available_at")
        result["workerId"] = result.pop("worker_id")
        result["lastError"] = result.pop("last_error")
        result["createdAt"] = result.pop("created_at")
        result["updatedAt"] = result.pop("updated_at")
        return result

    def get(self, task_id: str, *, tenant_id: str | None = None) -> dict[str, Any] | None:
        with self._connect() as conn:
            query, params = "SELECT * FROM tasks WHERE id=?", [task_id]
            if tenant_id is not None:
                query += " AND tenant_id=?"
                params.append(self._tenant(tenant_id))
            return self._task(conn.execute(query, params).fetchone())

    @staticmethod
    def _tenant(value: str) -> str:
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 200:
            raise ValueError("tenant_id must not be blank")
        return value.strip()

    @staticmethod
    def _payload(value: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError("task payload must be an object")
        if any(key not in _REFERENCE_KEYS for key in value):
            raise ValueError("task payload may contain reference fields only")
        for key, item in value.items():
            if key == "version":
                if isinstance(item, bool) or not isinstance(item, int) or item < 1:
                    raise ValueError("task payload version must be a positive integer")
            elif not isinstance(item, str) or not item.strip():
                raise ValueError("task payload references must be nonblank strings")
        return value

    def enqueue(self, task_id: str | None = None, *, kind: str, payload: dict[str, Any], tenant_id: str = "local", max_attempts: int = 3, now=None) -> dict[str, Any]:
        if task_id is None:
            task_id = uuid.uuid4().hex
        if not isinstance(task_id, str) or not task_id.strip():
            raise ValueError("task id must not be blank")
        if not isinstance(kind, str) or not kind.strip() or len(kind) > 100:
            raise ValueError("task kind must not be blank")
        tenant_id = self._tenant(tenant_id)
        payload = self._payload(payload)
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 20:
            raise ValueError("max_attempts must be between 1 and 20")
        timestamp = _iso(now)
        with self._connect(write=True) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO tasks(id,tenant_id,kind,payload_json,status,attempts,max_attempts,available_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (task_id, tenant_id, kind.strip(), json.dumps(payload, ensure_ascii=False, separators=(",", ":")), "queued", 0, max_attempts, timestamp, timestamp, timestamp),
            )
            return self._task(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())  # type: ignore[return-value]

    def claim(self, worker_id: str, *, task_id: str | None = None, tenant_id: str | None = None, now=None) -> dict[str, Any] | None:
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id must not be blank")
        if tenant_id is not None:
            tenant_id = self._tenant(tenant_id)
        timestamp = _iso(now)
        with self._connect(write=True) as conn:
            query = "SELECT * FROM tasks WHERE status IN ('queued','retry_wait') AND available_at <= ?"
            params: list[Any] = [timestamp]
            if task_id is not None:
                query += " AND id=?"
                params.append(task_id)
            if tenant_id is not None:
                query += " AND tenant_id=?"
                params.append(tenant_id)
            query += " ORDER BY available_at, created_at, id LIMIT 1"
            row = conn.execute(query, params).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE tasks SET status='running', attempts=attempts+1, worker_id=?, updated_at=? WHERE id=? AND status IN ('queued','retry_wait')",
                (worker_id.strip(), timestamp, row["id"]),
            )
            return self._task(conn.execute("SELECT * FROM tasks WHERE id=?", (row["id"],)).fetchone())

    def ack(self, task_id: str, *, worker_id: str | None = None, tenant_id: str | None = None, now=None) -> dict[str, Any] | None:
        if worker_id is not None and (not isinstance(worker_id, str) or not worker_id.strip()):
            raise ValueError("worker_id must not be blank")
        worker_id = worker_id.strip() if worker_id is not None else None
        timestamp = _iso(now)
        with self._connect(write=True) as conn:
            query, params = "SELECT * FROM tasks WHERE id=?", [task_id]
            if tenant_id is not None:
                query += " AND tenant_id=?"
                params.append(self._tenant(tenant_id))
            row = conn.execute(query, params).fetchone()
            if row is None:
                return None
            if row["status"] == "succeeded":
                return self._task(row)
            if row["status"] != "running":
                raise ValueError("only running tasks can be acknowledged")
            if worker_id is not None and row["worker_id"] != worker_id:
                return None
            if worker_id is None:
                conn.execute("UPDATE tasks SET status='succeeded', worker_id=NULL, updated_at=? WHERE id=? AND status='running'", (timestamp, task_id))
            else:
                conn.execute("UPDATE tasks SET status='succeeded', worker_id=NULL, updated_at=? WHERE id=? AND status='running' AND worker_id=?", (timestamp, task_id, worker_id))
            return self._task(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())

    def fail(self, task_id: str, error: str, *, worker_id: str | None = None, tenant_id: str | None = None, now=None, delay_seconds: int = 30) -> dict[str, Any] | None:
        if not isinstance(error, str) or not error.strip():
            raise ValueError("error must not be blank")
        if worker_id is not None and (not isinstance(worker_id, str) or not worker_id.strip()):
            raise ValueError("worker_id must not be blank")
        worker_id = worker_id.strip() if worker_id is not None else None
        if isinstance(delay_seconds, bool) or not isinstance(delay_seconds, int) or not 0 <= delay_seconds <= 86400:
            raise ValueError("delay_seconds must be between 0 and 86400")
        moment = _now(now)
        timestamp = moment.isoformat()
        with self._connect(write=True) as conn:
            query, params = "SELECT * FROM tasks WHERE id=?", [task_id]
            if tenant_id is not None:
                query += " AND tenant_id=?"
                params.append(self._tenant(tenant_id))
            row = conn.execute(query, params).fetchone()
            if row is None:
                return None
            if row["status"] == "dead":
                return self._task(row)
            if row["status"] != "running":
                raise ValueError("only running tasks can fail")
            if worker_id is not None and row["worker_id"] != worker_id:
                return None
            terminal = row["attempts"] >= row["max_attempts"]
            status = "dead" if terminal else "retry_wait"
            available = timestamp if terminal else (moment + timedelta(seconds=delay_seconds)).isoformat()
            if worker_id is None:
                conn.execute("UPDATE tasks SET status=?, available_at=?, worker_id=NULL, last_error=?, updated_at=? WHERE id=? AND status='running'", (status, available, error.strip()[:2000], timestamp, task_id))
            else:
                conn.execute("UPDATE tasks SET status=?, available_at=?, worker_id=NULL, last_error=?, updated_at=? WHERE id=? AND status='running' AND worker_id=?", (status, available, error.strip()[:2000], timestamp, task_id, worker_id))
            return self._task(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())

    def cancel(self, task_id: str, *, tenant_id: str | None = None, now=None) -> dict[str, Any] | None:
        timestamp = _iso(now)
        with self._connect(write=True) as conn:
            query, params = "SELECT * FROM tasks WHERE id=?", [task_id]
            if tenant_id is not None:
                query += " AND tenant_id=?"
                params.append(self._tenant(tenant_id))
            row = conn.execute(query, params).fetchone()
            if row is None:
                return None
            if row["status"] in _TERMINAL:
                return self._task(row)
            conn.execute("UPDATE tasks SET status='cancelled', worker_id=NULL, updated_at=? WHERE id=?", (timestamp, task_id))
            return self._task(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())

    def retry_dead(self, task_id: str, *, tenant_id: str | None = None, now=None) -> dict[str, Any] | None:
        timestamp = _iso(now)
        with self._connect(write=True) as conn:
            query, params = "SELECT * FROM tasks WHERE id=?", [task_id]
            if tenant_id is not None:
                query += " AND tenant_id=?"
                params.append(self._tenant(tenant_id))
            row = conn.execute(query, params).fetchone()
            if row is None:
                return None
            if row["status"] != "dead":
                raise ValueError("only dead-letter tasks can be retried")
            conn.execute("UPDATE tasks SET status='queued', attempts=0, available_at=?, worker_id=NULL, last_error=NULL, updated_at=? WHERE id=?", (timestamp, timestamp, task_id))
            return self._task(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())

    def requeue_stale(self, now, *, lease_seconds: int = 300, tenant_id: str | None = None) -> list[str]:
        """Return abandoned running tasks to the retry queue after a worker lease expires."""
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 1 <= lease_seconds <= 86400:
            raise ValueError("lease_seconds must be between 1 and 86400")
        moment = _now(now)
        if tenant_id is not None:
            tenant_id = self._tenant(tenant_id)
        cutoff = (moment - timedelta(seconds=lease_seconds)).isoformat()
        timestamp = moment.isoformat()
        with self._connect(write=True) as conn:
            query, params = "SELECT id FROM tasks WHERE status='running' AND updated_at < ?", [cutoff]
            if tenant_id is not None:
                query += " AND tenant_id=?"
                params.append(tenant_id)
            query += " ORDER BY updated_at, id"
            rows = conn.execute(query, params).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                query, params = "UPDATE tasks SET status='retry_wait', worker_id=NULL, available_at=?, last_error=?, updated_at=? WHERE status='running' AND updated_at < ?", [timestamp, "worker lease expired", timestamp, cutoff]
                if tenant_id is not None:
                    query += " AND tenant_id=?"
                    params.append(tenant_id)
                conn.execute(query, params)
            return ids

    def heartbeat(self, task_id: str, worker_id: str, *, tenant_id: str | None = None, now=None) -> dict[str, Any] | None:
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id must not be blank")
        worker_id = worker_id.strip()
        timestamp = _iso(now)
        with self._connect(write=True) as conn:
            query, params = "SELECT * FROM tasks WHERE id=?", [task_id]
            if tenant_id is not None:
                query += " AND tenant_id=?"
                params.append(self._tenant(tenant_id))
            row = conn.execute(query, params).fetchone()
            if row is None:
                return None
            if row["status"] != "running" or row["worker_id"] != worker_id:
                return None
            conn.execute("UPDATE tasks SET updated_at=? WHERE id=? AND status='running' AND worker_id=?", (timestamp, task_id, worker_id.strip()))
            return self._task(conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())

    def dead_letters(self, *, kind: str | None = None, tenant_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        query = "SELECT * FROM tasks WHERE status='dead'"
        params: list[Any] = []
        if kind is not None:
            query += " AND kind=?"
            params.append(kind)
        if tenant_id is not None:
            query += " AND tenant_id=?"
            params.append(self._tenant(tenant_id))
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            return [self._task(row) for row in conn.execute(query, params).fetchall()]

    def list_tasks(self, *, status: str | None = None, kind: str | None = None, tenant_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        """Return operational metadata for the queue dashboard.

        The returned payload is still restricted to the caller supplied task
        reference.  This method deliberately does not expose any worker input
        beyond the queue's metadata contract.
        """
        if status is not None and status not in _ACTIVE | _TERMINAL:
            raise ValueError("invalid task status")
        if kind is not None and (not isinstance(kind, str) or not kind.strip()):
            raise ValueError("kind must not be blank")
        if tenant_id is not None:
            tenant_id = self._tenant(tenant_id)
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        query = "SELECT * FROM tasks WHERE 1=1"
        params: list[Any] = []
        if status is not None:
            query += " AND status=?"
            params.append(status)
        if kind is not None:
            query += " AND kind=?"
            params.append(kind.strip())
        if tenant_id is not None:
            query += " AND tenant_id=?"
            params.append(tenant_id)
        query += " ORDER BY updated_at DESC, id LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            return [self._task(row) for row in conn.execute(query, params).fetchall()]
