"""SQLite persistence for immutable, tenant-scoped Playbook versions."""
from __future__ import annotations

from contextlib import closing, contextmanager
import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from .playbooks import PlaybookContent, PlaybookSnapshot, ReviewScope, content_hash


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


class PlaybookStore:
    """Version and lifecycle adapter backed by one SQLite database."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def _write_transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialize(self) -> None:
        with closing(self._connect()) as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS playbook_versions (
                    tenant_id TEXT NOT NULL,
                    playbook_id TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK (version > 0),
                    status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'archived')),
                    content_json TEXT NOT NULL,
                    content_hash TEXT,
                    created_at TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    published_at TEXT,
                    published_by TEXT,
                    archived_at TEXT,
                    archived_by TEXT,
                    snapshot_json TEXT,
                    PRIMARY KEY (tenant_id, playbook_id, version)
                );
                CREATE INDEX IF NOT EXISTS idx_playbook_versions_tenant_book
                    ON playbook_versions(tenant_id, playbook_id, version DESC);
                CREATE TABLE IF NOT EXISTS playbook_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tenant_id TEXT NOT NULL,
                    playbook_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    type TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_playbook_events_tenant_book
                    ON playbook_events(tenant_id, playbook_id, id);
                """
            )

    @staticmethod
    def _content_json(content: PlaybookContent) -> str:
        content = PlaybookContent.model_validate(content.model_dump(mode="json"))
        return json.dumps(
            content.model_dump(mode="json"),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )

    @staticmethod
    def _version_dict(row: sqlite3.Row) -> dict[str, Any]:
        content = PlaybookContent.model_validate_json(row["content_json"])
        snapshot: dict[str, Any] | None = None
        if row["snapshot_json"] is not None:
            parsed_snapshot = PlaybookSnapshot.model_validate_json(row["snapshot_json"])
            if parsed_snapshot.content != content or parsed_snapshot.contentHash != row["content_hash"]:
                raise ValueError("stored snapshot does not match version content")
            snapshot = parsed_snapshot.model_dump(mode="json")
            identity = {
                "playbookId": row["playbook_id"], "tenantId": row["tenant_id"], "version": row["version"],
                "createdAt": row["created_at"], "createdBy": row["created_by"],
                "publishedAt": row["published_at"], "publishedBy": row["published_by"],
            }
            if any(snapshot[key] != value for key, value in identity.items()):
                raise ValueError("stored snapshot identity does not match version record")
        return {
            "playbookId": row["playbook_id"],
            "tenantId": row["tenant_id"],
            "version": row["version"],
            "status": row["status"],
            "content": content.model_dump(mode="json"),
            "contentHash": row["content_hash"],
            "createdAt": row["created_at"],
            "createdBy": row["created_by"],
            "updatedAt": row["updated_at"],
            "updatedBy": row["updated_by"],
            "publishedAt": row["published_at"],
            "publishedBy": row["published_by"],
            "archivedAt": row["archived_at"],
            "archivedBy": row["archived_by"],
            "snapshot": snapshot,
        }

    @staticmethod
    def _required_row(
        conn: sqlite3.Connection, playbook_id: str, version: int, tenant_id: str
    ) -> sqlite3.Row:
        row = conn.execute(
            """SELECT * FROM playbook_versions
               WHERE tenant_id = ? AND playbook_id = ? AND version = ?""",
            (tenant_id, playbook_id, version),
        ).fetchone()
        if row is None:
            raise KeyError(f"playbook version not found: {playbook_id} v{version}")
        return row

    @staticmethod
    def _append_event(
        conn: sqlite3.Connection,
        *,
        tenant_id: str,
        playbook_id: str,
        version: int,
        kind: str,
        subject: str,
        created_at: str,
    ) -> None:
        conn.execute(
            """INSERT INTO playbook_events
               (tenant_id, playbook_id, version, type, subject, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (tenant_id, playbook_id, version, kind, subject, created_at),
        )

    def create_draft(
        self, content: PlaybookContent, *, tenant_id: str, subject: str
    ) -> dict[str, Any]:
        playbook_id = uuid.uuid4().hex
        now = _utc_now()
        with self._write_transaction() as conn:
            conn.execute(
                """INSERT INTO playbook_versions
                   (tenant_id, playbook_id, version, status, content_json,
                    created_at, created_by, updated_at, updated_by)
                   VALUES (?, ?, 1, 'draft', ?, ?, ?, ?, ?)""",
                (tenant_id, playbook_id, self._content_json(content), now, subject, now, subject),
            )
            self._append_event(
                conn,
                tenant_id=tenant_id,
                playbook_id=playbook_id,
                version=1,
                kind="created",
                subject=subject,
                created_at=now,
            )
        return self.get_version(playbook_id, 1, tenant_id=tenant_id)  # type: ignore[return-value]

    def update_draft(
        self,
        playbook_id: str,
        version: int,
        content: PlaybookContent,
        *,
        tenant_id: str,
        subject: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._write_transaction() as conn:
            row = self._required_row(conn, playbook_id, version, tenant_id)
            if row["status"] != "draft":
                raise ValueError("only draft versions can be edited")
            conn.execute(
                """UPDATE playbook_versions
                   SET content_json = ?, updated_at = ?, updated_by = ?
                   WHERE tenant_id = ? AND playbook_id = ? AND version = ?""",
                (self._content_json(content), now, subject, tenant_id, playbook_id, version),
            )
            self._append_event(
                conn,
                tenant_id=tenant_id,
                playbook_id=playbook_id,
                version=version,
                kind="updated",
                subject=subject,
                created_at=now,
            )
        return self.get_version(playbook_id, version, tenant_id=tenant_id)  # type: ignore[return-value]

    def publish(
        self, playbook_id: str, version: int, *, tenant_id: str, subject: str
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._write_transaction() as conn:
            row = self._required_row(conn, playbook_id, version, tenant_id)
            if row["status"] != "draft":
                raise ValueError("only draft versions can be published")
            content = PlaybookContent.model_validate_json(row["content_json"])
            if not content.rules:
                raise ValueError("cannot publish a playbook without rules")
            digest = content_hash(content)
            snapshot = PlaybookSnapshot(
                playbookId=playbook_id,
                tenantId=tenant_id,
                version=version,
                content=content,
                contentHash=digest,
                createdAt=row["created_at"],
                createdBy=row["created_by"],
                publishedAt=now,
                publishedBy=subject,
            )
            conn.execute(
                """UPDATE playbook_versions
                   SET status = 'active', content_hash = ?, snapshot_json = ?,
                       published_at = ?, published_by = ?, updated_at = ?, updated_by = ?
                   WHERE tenant_id = ? AND playbook_id = ? AND version = ?""",
                (
                    digest,
                    json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    now,
                    subject,
                    now,
                    subject,
                    tenant_id,
                    playbook_id,
                    version,
                ),
            )
            self._append_event(
                conn,
                tenant_id=tenant_id,
                playbook_id=playbook_id,
                version=version,
                kind="published",
                subject=subject,
                created_at=now,
            )
        return self.get_version(playbook_id, version, tenant_id=tenant_id)  # type: ignore[return-value]

    def create_version(
        self, playbook_id: str, *, source_version: int, tenant_id: str, subject: str
    ) -> dict[str, Any]:
        """Fork content into a fresh draft; serialize allocation across writers."""
        now = _utc_now()
        with self._write_transaction() as conn:
            source = self._required_row(conn, playbook_id, source_version, tenant_id)
            # Validate persisted content and snapshot before copying its policy.
            self._version_dict(source)
            version = conn.execute(
                "SELECT MAX(version) + 1 FROM playbook_versions WHERE tenant_id = ? AND playbook_id = ?",
                (tenant_id, playbook_id),
            ).fetchone()[0]
            conn.execute(
                """INSERT INTO playbook_versions
                   (tenant_id, playbook_id, version, status, content_json,
                    created_at, created_by, updated_at, updated_by)
                   VALUES (?, ?, ?, 'draft', ?, ?, ?, ?, ?)""",
                (tenant_id, playbook_id, version, source["content_json"], now, subject, now, subject),
            )
            self._append_event(
                conn, tenant_id=tenant_id, playbook_id=playbook_id, version=version,
                kind="created", subject=subject, created_at=now,
            )
        return self.get_version(playbook_id, version, tenant_id=tenant_id)  # type: ignore[return-value]

    def archive(
        self, playbook_id: str, version: int, *, tenant_id: str, subject: str
    ) -> dict[str, Any]:
        now = _utc_now()
        with self._write_transaction() as conn:
            row = self._required_row(conn, playbook_id, version, tenant_id)
            if row["status"] == "archived":
                raise ValueError("playbook version is already archived")
            conn.execute(
                """UPDATE playbook_versions
                   SET status = 'archived', archived_at = ?, archived_by = ?,
                       updated_at = ?, updated_by = ?
                   WHERE tenant_id = ? AND playbook_id = ? AND version = ?""",
                (now, subject, now, subject, tenant_id, playbook_id, version),
            )
            self._append_event(
                conn,
                tenant_id=tenant_id,
                playbook_id=playbook_id,
                version=version,
                kind="archived",
                subject=subject,
                created_at=now,
            )
        return self.get_version(playbook_id, version, tenant_id=tenant_id)  # type: ignore[return-value]

    def get_version(
        self, playbook_id: str, version: int, *, tenant_id: str
    ) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                """SELECT * FROM playbook_versions
                   WHERE tenant_id = ? AND playbook_id = ? AND version = ?""",
                (tenant_id, playbook_id, version),
            ).fetchone()
        return self._version_dict(row) if row is not None else None

    def list_versions(self, playbook_id: str, *, tenant_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM playbook_versions WHERE tenant_id = ? AND playbook_id = ? ORDER BY version DESC",
                (tenant_id, playbook_id),
            ).fetchall()
        return [self._version_dict(row) for row in rows]

    def list_playbooks(self, *, tenant_id: str, active_only: bool = False) -> list[dict[str, Any]]:
        """Return one version per book, optionally preferring the latest active version."""
        with closing(self._connect()) as conn:
            if active_only:
                query = """SELECT v.* FROM playbook_versions v
                    WHERE v.tenant_id = ? AND v.status = 'active' AND v.version = (
                        SELECT MAX(latest.version) FROM playbook_versions latest
                        WHERE latest.tenant_id = v.tenant_id AND latest.playbook_id = v.playbook_id
                          AND latest.status = 'active'
                    ) ORDER BY v.updated_at DESC, v.playbook_id"""
            else:
                query = """SELECT v.* FROM playbook_versions v
                    WHERE v.tenant_id = ? AND v.version = (
                        SELECT MAX(latest.version) FROM playbook_versions latest
                        WHERE latest.tenant_id = v.tenant_id AND latest.playbook_id = v.playbook_id
                    ) ORDER BY v.updated_at DESC, v.playbook_id"""
            rows = conn.execute(query, (tenant_id,)).fetchall()
        return [self._version_dict(row) for row in rows]

    def resolve_snapshot(
        self, playbook_id: str, version: int, *, tenant_id: str, scope: ReviewScope | None = None,
        contract_type: str | None = None, jurisdiction: str | None = None,
        business_scenario: str | None = None, effective_scope: str | None = None,
    ) -> PlaybookSnapshot:
        """Select an active revision once; callers persist it before queueing work."""
        row = self.get_version(playbook_id, version, tenant_id=tenant_id)
        if row is None:
            raise KeyError("playbook version not found")
        if row["status"] != "active":
            raise ValueError("playbook version must be active")
        if scope is None:
            if None in {contract_type, jurisdiction, business_scenario, effective_scope}:
                raise ValueError("review scope is required")
            scope = ReviewScope(contractType=contract_type, jurisdiction=jurisdiction, businessScenario=business_scenario, effectiveScope=effective_scope)
        snapshot = PlaybookSnapshot.model_validate(row["snapshot"])
        content = snapshot.content
        if (
            content.contractType != scope.contractType
            or content.jurisdiction != scope.jurisdiction
            or content.businessScenario != scope.businessScenario
            or ("*" not in content.effectiveScope and scope.effectiveScope not in content.effectiveScope)
        ):
            raise ValueError("playbook scope does not match review request")
        return snapshot

    def list_events(self, playbook_id: str, *, tenant_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """SELECT id, playbook_id, tenant_id, version, type, subject, created_at
                   FROM playbook_events
                   WHERE tenant_id = ? AND playbook_id = ? ORDER BY id""",
                (tenant_id, playbook_id),
            ).fetchall()
        return [
            {
                "id": row["id"],
                "playbookId": row["playbook_id"],
                "tenantId": row["tenant_id"],
                "version": row["version"],
                "type": row["type"],
                "subject": row["subject"],
                "createdAt": row["created_at"],
            }
            for row in rows
        ]

    def retention_candidates(self, *, tenant_id: str, before) -> list[dict[str, Any]]:
        """Return archived version identifiers without exposing rule bodies."""
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id must not be blank")
        if isinstance(before, datetime):
            cutoff = before
        elif isinstance(before, str):
            raw = before.strip().replace("Z", "+00:00")
            try:
                cutoff = datetime.fromisoformat(raw)
            except ValueError as exc:
                raise ValueError("before must be ISO datetime") from exc
        else:
            raise ValueError("before must be ISO datetime")
        if cutoff.tzinfo is None or cutoff.utcoffset() is None:
            raise ValueError("before must be timezone-aware")
        cutoff = cutoff.astimezone(UTC)
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT tenant_id,playbook_id,version,status,created_at FROM playbook_versions WHERE tenant_id=? AND status='archived'", (tenant_id,)).fetchall()
        result = []
        for row in rows:
            created_at = datetime.fromisoformat(row["created_at"]).astimezone(UTC)
            if created_at < cutoff:
                result.append({"resourceType": "playbook_version", "resourceId": f"{row['playbook_id']}:{row['version']}", "tenantId": row["tenant_id"], "createdAt": row["created_at"], "status": row["status"]})
        return sorted(result, key=lambda item: (item["createdAt"], item["resourceId"]))

    def purge_version(self, playbook_id: str, version: int, *, tenant_id: str) -> bool:
        with self._write_transaction() as conn:
            row = conn.execute("SELECT status FROM playbook_versions WHERE tenant_id=? AND playbook_id=? AND version=?", (tenant_id, playbook_id, version)).fetchone()
            if row is None or row["status"] != "archived":
                return False
            conn.execute("DELETE FROM playbook_events WHERE tenant_id=? AND playbook_id=? AND version=?", (tenant_id, playbook_id, version))
            conn.execute("DELETE FROM playbook_versions WHERE tenant_id=? AND playbook_id=? AND version=?", (tenant_id, playbook_id, version))
            return True


__all__ = ["PlaybookStore"]
