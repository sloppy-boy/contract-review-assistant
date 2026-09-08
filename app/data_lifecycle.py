"""Tenant-scoped legal holds and auditable retention decisions.

The store contains identifiers and audit metadata only. Purge callers provide
resource records and a deletion callback so raw contract bytes never enter this
database and dry-runs can be reviewed before a destructive operation.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable


def _moment(value: str | datetime | None = None) -> datetime:
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
    return _moment(parsed)


def _iso(value: str | datetime | None = None) -> str:
    return _moment(value).isoformat()


class DataLifecycleStore:
    """Append-only hold/deletion ledger with tenant-scoped purge selection."""

    def __init__(self, path: Path | str):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect(write=True) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS legal_holds (
                    tenant_id TEXT NOT NULL, resource_type TEXT NOT NULL, resource_id TEXT NOT NULL,
                    reason TEXT NOT NULL, created_by TEXT NOT NULL, created_at TEXT NOT NULL,
                    released_by TEXT, released_at TEXT,
                    PRIMARY KEY(tenant_id, resource_type, resource_id)
                );
                CREATE TABLE IF NOT EXISTS lifecycle_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, subject TEXT NOT NULL,
                    action TEXT NOT NULL, resource_type TEXT NOT NULL, resource_id TEXT NOT NULL,
                    detail TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS lifecycle_events_tenant ON lifecycle_events(tenant_id, id);
                """
            )

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
    def _hold(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "tenantId": row["tenant_id"], "resourceType": row["resource_type"], "resourceId": row["resource_id"],
            "reason": row["reason"], "createdBy": row["created_by"], "createdAt": row["created_at"],
            "releasedBy": row["released_by"], "releasedAt": row["released_at"],
        }

    def _event(self, conn, *, tenant_id: str, subject: str, action: str, resource_type: str, resource_id: str, detail: str, created_at: str) -> None:
        conn.execute("INSERT INTO lifecycle_events(tenant_id,subject,action,resource_type,resource_id,detail,created_at) VALUES(?,?,?,?,?,?,?)", (tenant_id, subject, action, resource_type, resource_id, detail[:2000], created_at))

    def place_hold(self, *, tenant_id: str, resource_type: str, resource_id: str, reason: str, actor: str, now=None) -> dict[str, Any]:
        values = (tenant_id, resource_type, resource_id, reason, actor)
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("hold fields must not be blank")
        timestamp = _iso(now)
        with self._connect(write=True) as conn:
            row = conn.execute("SELECT * FROM legal_holds WHERE tenant_id=? AND resource_type=? AND resource_id=?", values[:3]).fetchone()
            if row is not None and row["released_at"] is None:
                return self._hold(row)  # idempotent duplicate request
            conn.execute("INSERT OR REPLACE INTO legal_holds(tenant_id,resource_type,resource_id,reason,created_by,created_at,released_by,released_at) VALUES(?,?,?,?,?,?,NULL,NULL)", (tenant_id, resource_type, resource_id, reason.strip()[:2000], actor, timestamp))
            self._event(conn, tenant_id=tenant_id, subject=actor, action="hold.created", resource_type=resource_type, resource_id=resource_id, detail=reason, created_at=timestamp)
            return self._hold(conn.execute("SELECT * FROM legal_holds WHERE tenant_id=? AND resource_type=? AND resource_id=?", values[:3]).fetchone())  # type: ignore[return-value]

    def release_hold(self, tenant_id: str, resource_type: str, resource_id: str, *, actor: str, now=None) -> dict[str, Any] | None:
        timestamp = _iso(now)
        with self._connect(write=True) as conn:
            row = conn.execute("SELECT * FROM legal_holds WHERE tenant_id=? AND resource_type=? AND resource_id=?", (tenant_id, resource_type, resource_id)).fetchone()
            if row is None:
                return None
            if row["released_at"] is None:
                conn.execute("UPDATE legal_holds SET released_by=?, released_at=? WHERE tenant_id=? AND resource_type=? AND resource_id=?", (actor, timestamp, tenant_id, resource_type, resource_id))
                self._event(conn, tenant_id=tenant_id, subject=actor, action="hold.released", resource_type=resource_type, resource_id=resource_id, detail="", created_at=timestamp)
            return self._hold(conn.execute("SELECT * FROM legal_holds WHERE tenant_id=? AND resource_type=? AND resource_id=?", (tenant_id, resource_type, resource_id)).fetchone())

    def is_held(self, tenant_id: str, resource_type: str, resource_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT 1 FROM legal_holds WHERE tenant_id=? AND resource_type=? AND resource_id=? AND released_at IS NULL", (tenant_id, resource_type, resource_id)).fetchone()
        return row is not None

    def holds(self, tenant_id: str, *, active_only: bool = True) -> list[dict[str, Any]]:
        query = "SELECT * FROM legal_holds WHERE tenant_id=?"
        if active_only:
            query += " AND released_at IS NULL"
        query += " ORDER BY created_at DESC"
        with self._connect() as conn:
            return [self._hold(row) for row in conn.execute(query, (tenant_id,)).fetchall()]

    def events(self, tenant_id: str, *, limit: int = 500) -> list[dict[str, Any]]:
        if not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self._connect() as conn:
            rows = conn.execute("SELECT subject,action,resource_type,resource_id,detail,created_at FROM lifecycle_events WHERE tenant_id=? ORDER BY id DESC LIMIT ?", (tenant_id, limit)).fetchall()
        return [{"subject": row["subject"], "action": row["action"], "resourceType": row["resource_type"], "resourceId": row["resource_id"], "detail": row["detail"], "createdAt": row["created_at"]} for row in reversed(rows)]

    def purge(self, records: Iterable[dict[str, Any]], *, tenant_id: str, before, actor: str, delete: Callable[[dict[str, Any]], Any], dry_run: bool = False) -> dict[str, list[str]]:
        cutoff = _moment(before)
        if not isinstance(tenant_id, str) or not tenant_id.strip() or not isinstance(actor, str) or not actor.strip():
            raise ValueError("tenant_id and actor must not be blank")
        eligible: list[str] = []
        held: list[str] = []
        deleted: list[str] = []
        for record in records:
            if not isinstance(record, dict) or record.get("tenantId") != tenant_id:
                continue
            resource_type, resource_id = record.get("resourceType"), record.get("resourceId")
            if not isinstance(resource_type, str) or not resource_type.strip() or not isinstance(resource_id, str) or not resource_id.strip():
                raise ValueError("retention records need resource type and id")
            if _moment(record.get("createdAt")) >= cutoff:
                continue
            # Hold creation and the destructive callback share this write
            # transaction.  A concurrent place_hold therefore serializes
            # before the eligibility decision instead of racing the delete.
            with self._connect(write=True) as conn:
                if conn.execute(
                    "SELECT 1 FROM legal_holds WHERE tenant_id=? AND resource_type=? AND resource_id=? AND released_at IS NULL",
                    (tenant_id, resource_type, resource_id),
                ).fetchone() is not None:
                    held.append(resource_id)
                    continue
                eligible.append(resource_id)
                if dry_run:
                    continue
                deleted_result = delete(record)
                if deleted_result is False:
                    continue
                timestamp = _iso()
                self._event(conn, tenant_id=tenant_id, subject=actor, action="resource.deleted", resource_type=resource_type, resource_id=resource_id, detail="retention purge", created_at=timestamp)
                deleted.append(resource_id)
        return {"eligible": eligible, "held": held, "deleted": [] if dry_run else deleted}
