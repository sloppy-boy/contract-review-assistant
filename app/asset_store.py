"""Tenant-scoped persistent storage for imported contract assets."""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.security import Principal


_MAX_ORIGINAL_BYTES = 10 * 1024 * 1024
_MAX_TEXT_CHARS = 1_000_000
_VISIBILITIES = {"private", "tenant"}
_OBLIGATION_KINDS = {"obligation", "renewal", "key_date"}
_WRITER_ROLES = {"requester", "legal_reviewer", "admin"}
_NO_ANSWER = "无法从可访问合同原文中找到依据。"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _nonblank(value: Any, field: str, *, maximum: int = 500) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    result = value.strip()
    if not result:
        raise ValueError(f"{field} must not be blank")
    if len(result) > maximum:
        raise ValueError(f"{field} is too long")
    return result


def _strings(values: Any, field: str, *, maximum: int = 100, item_maximum: int = 200) -> list[str]:
    if not isinstance(values, (list, tuple)) or len(values) > maximum:
        raise ValueError(f"invalid {field}")
    result: list[str] = []
    for value in values:
        item = _nonblank(value, field, maximum=item_maximum)
        if item not in result:
            result.append(item)
    return result


def _utc(value: Any, field: str) -> datetime:
    if isinstance(value, str):
        raw = value.strip()
        if raw.endswith(("Z", "z")):
            raw = raw[:-1] + "+00:00"
        try:
            value = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO datetime") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(UTC)


def _revision(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("invalid expected revision")
    return value


class AssetStore:
    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect(write=True) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL, revision INTEGER NOT NULL, visibility TEXT NOT NULL,
                    title TEXT NOT NULL, filename TEXT NOT NULL, original BLOB NOT NULL,
                    shared_json TEXT NOT NULL, tags_json TEXT NOT NULL, text TEXT NOT NULL,
                    segments_json TEXT NOT NULL, metadata_json TEXT NOT NULL,
                    warnings_json TEXT NOT NULL, method TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS assets_tenant ON assets(tenant_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS asset_versions (
                    asset_id TEXT NOT NULL REFERENCES assets(id), version INTEGER NOT NULL,
                    parent_version INTEGER, content_hash TEXT NOT NULL, filename TEXT NOT NULL,
                    original BLOB NOT NULL, created_at TEXT NOT NULL, created_by TEXT NOT NULL,
                    text TEXT NOT NULL, segments_json TEXT NOT NULL, metadata_json TEXT NOT NULL,
                    warnings_json TEXT NOT NULL, method TEXT NOT NULL,
                    PRIMARY KEY(asset_id, version)
                );
                CREATE TRIGGER IF NOT EXISTS immutable_asset_version_update
                BEFORE UPDATE ON asset_versions BEGIN SELECT RAISE(ABORT, 'asset versions are immutable'); END;
                CREATE TRIGGER IF NOT EXISTS immutable_asset_version_delete
                BEFORE DELETE ON asset_versions BEGIN SELECT RAISE(ABORT, 'asset versions are immutable'); END;
                CREATE TABLE IF NOT EXISTS obligations (
                    id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                    title TEXT NOT NULL, due_at TEXT NOT NULL, kind TEXT NOT NULL,
                    created_at TEXT NOT NULL, created_by TEXT NOT NULL, completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS obligations_asset ON obligations(asset_id, due_at);
                CREATE TABLE IF NOT EXISTS asset_notifications (
                    id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id) ON DELETE CASCADE,
                    obligation_id TEXT NOT NULL REFERENCES obligations(id) ON DELETE CASCADE,
                    tenant_id TEXT NOT NULL, recipient TEXT NOT NULL, created_at TEXT NOT NULL,
                    read_at TEXT, UNIQUE(obligation_id, tenant_id, recipient)
                );
                CREATE INDEX IF NOT EXISTS asset_notifications_recipient
                    ON asset_notifications(tenant_id, recipient, created_at DESC);
                CREATE TABLE IF NOT EXISTS asset_deletions (
                    asset_id TEXT PRIMARY KEY REFERENCES assets(id) ON DELETE CASCADE,
                    tenant_id TEXT NOT NULL, deleted_at TEXT NOT NULL, deleted_by TEXT NOT NULL, reason TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS asset_deletions_tenant ON asset_deletions(tenant_id, deleted_at);
                """
            )
            # Existing assets become v1 without rewriting their historical extraction.
            columns = {row['name'] for row in conn.execute('PRAGMA table_info(assets)')}
            if 'current_version' not in columns:
                conn.execute('ALTER TABLE assets ADD COLUMN current_version INTEGER NOT NULL DEFAULT 1')
            if 'content_hash' not in columns:
                conn.execute("ALTER TABLE assets ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''")
            for row in conn.execute("SELECT * FROM assets WHERE content_hash='' ").fetchall():
                digest = hashlib.sha256(bytes(row['original'])).hexdigest()
                conn.execute('UPDATE assets SET content_hash=? WHERE id=?', (digest, row['id']))
                self._insert_version(conn, self._row(conn, row['id']), row['created_by'], row['created_at'])

    @contextmanager
    def _connect(self, *, write: bool = False):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
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
    def _readable(row: sqlite3.Row, actor: Principal) -> bool:
        if row["tenant_id"] != actor.tenant_id:
            return False
        return (row["visibility"] == "tenant" or row["created_by"] == actor.subject
                or actor.role == "admin" or actor.subject in json.loads(row["shared_json"]))

    @staticmethod
    def _writable(row: sqlite3.Row, actor: Principal) -> bool:
        return (row["tenant_id"] == actor.tenant_id and actor.role in _WRITER_ROLES
                and (row["created_by"] == actor.subject or actor.role == "admin"))

    def _row(self, conn: sqlite3.Connection, asset_id: str) -> sqlite3.Row | None:
        return conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()

    def _is_deleted(self, asset_id: str) -> bool:
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM asset_deletions WHERE asset_id=?", (asset_id,)).fetchone() is not None

    def _visible_row(self, conn: sqlite3.Connection, asset_id: str, actor: Principal) -> sqlite3.Row:
        row = self._row(conn, asset_id)
        deleted = conn.execute("SELECT 1 FROM asset_deletions WHERE asset_id=?", (asset_id,)).fetchone()
        if row is None or deleted is not None or not self._readable(row, actor):
            raise KeyError(asset_id)
        return row

    def _write_row(self, conn: sqlite3.Connection, asset_id: str, actor: Principal) -> sqlite3.Row:
        row = self._visible_row(conn, asset_id, actor)
        if not self._writable(row, actor):
            raise PermissionError("asset is not writable")
        return row

    @staticmethod
    def _asset(row: sqlite3.Row, *, summary: bool = False) -> dict[str, Any]:
        result = {
            "id": row["id"], "title": row["title"], "filename": row["filename"],
            "tenantId": row["tenant_id"], "createdBy": row["created_by"],
            "createdAt": row["created_at"], "revision": row["revision"],
            "visibility": row["visibility"], "sharedWith": json.loads(row["shared_json"]),
            "tags": json.loads(row["tags_json"]), "metadata": json.loads(row["metadata_json"]),
            "warnings": json.loads(row["warnings_json"]), "method": row["method"],
            "version": row["current_version"], "parentVersion": row["current_version"] - 1 or None,
            "contentHash": row["content_hash"],
        }
        if not summary:
            result.update(text=row["text"], segments=json.loads(row["segments_json"]))
        return result

    @staticmethod
    def _prepare_document(filename, data, extracted):
        filename = _nonblank(filename, "filename")
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        if not data or len(data) > _MAX_ORIGINAL_BYTES:
            raise ValueError("document must contain 1 byte to 10 MiB")
        if not isinstance(extracted, dict):
            raise TypeError("extracted must be a mapping")
        text = extracted.get("text")
        segments = extracted.get("segments")
        metadata = extracted.get("metadata", {})
        warnings = extracted.get("warnings", [])
        method = _nonblank(extracted.get("method"), "method", maximum=100)
        if not isinstance(text, str) or not text.strip() or len(text) > _MAX_TEXT_CHARS:
            raise ValueError("invalid extracted text")
        if not isinstance(segments, list) or not segments:
            raise ValueError("segments must not be empty")
        normalized_segments = []
        segment_ids: set[str] = set()
        segment_chars = 0
        for segment in segments:
            if not isinstance(segment, dict):
                raise ValueError("invalid segment")
            segment_id = _nonblank(segment.get("id"), "segment id", maximum=200)
            if segment_id in segment_ids:
                raise ValueError("duplicate segment id")
            location = segment.get("location")
            if location is None or location == "" or location == {} or location == []:
                raise ValueError("segment location must not be empty")
            segment_text = _nonblank(segment.get("text"), "segment text", maximum=_MAX_TEXT_CHARS)
            segment_chars += len(segment_text)
            if segment_chars > _MAX_TEXT_CHARS:
                raise ValueError("segments exceed 1,000,000 characters")
            segment_ids.add(segment_id)
            normalized_segments.append({"id": segment_id, "location": location, "text": segment_text})
        if not isinstance(metadata, dict) or not isinstance(warnings, list):
            raise ValueError("invalid extraction metadata")
        return filename, data, {"text": text, "segments": normalized_segments, "metadata": metadata, "warnings": warnings, "method": method}

    def create(self, filename, data, extracted, *, actor: Principal, visibility="private", tags=[], shared_with=[]):
        filename = _nonblank(filename, "filename")
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        if not data:
            raise ValueError("data must not be empty")
        if len(data) > _MAX_ORIGINAL_BYTES:
            raise ValueError("document exceeds 10 MiB")
        if actor.role not in _WRITER_ROLES:
            raise PermissionError("role cannot create assets")
        if visibility not in _VISIBILITIES:
            raise ValueError("invalid visibility")
        tags = _strings(tags, "tags", maximum=30, item_maximum=80)
        shared_with = _strings(shared_with, "shared_with")
        filename, data, document = self._prepare_document(filename, data, extracted)
        text, normalized_segments = document['text'], document['segments']
        metadata, warnings, method = document['metadata'], document['warnings'], document['method']
        asset_id = uuid.uuid4().hex
        now = datetime.now(UTC).isoformat()
        title = filename
        with self._connect(write=True) as conn:
            conn.execute(
                "INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (asset_id, actor.tenant_id, actor.subject, now, 1, visibility, title, filename,
                 data, _json(shared_with), _json(tags), text, _json(normalized_segments),
                 _json(metadata), _json(warnings), method, 1, hashlib.sha256(data).hexdigest()),
            )
            row = self._row(conn, asset_id)
            self._insert_version(conn, row, actor.subject, now)
            return self._asset(row)

    def get(self, id, *, actor: Principal):
        with self._connect() as conn:
            return self._asset(self._visible_row(conn, id, actor))

    def exists_for_tenant(self, id, *, tenant_id: str) -> bool:
        """Metadata-only existence check for governance operations."""
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM assets WHERE id=? AND tenant_id=? AND NOT EXISTS (SELECT 1 FROM asset_deletions WHERE asset_id=assets.id)", (id, tenant_id)).fetchone() is not None

    def list_assets(self, *, actor: Principal, query="", tag=""):
        query = query.strip().casefold() if isinstance(query, str) else ""
        tag = tag.strip() if isinstance(tag, str) else ""
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM assets WHERE tenant_id=? ORDER BY created_at DESC", (actor.tenant_id,)).fetchall()
        result = []
        for row in rows:
            if self._is_deleted(row["id"]):
                continue
            if not self._readable(row, actor):
                continue
            asset = self._asset(row, summary=True)
            if query and query not in (asset["title"] + " " + asset["filename"] + " " + " ".join(asset["tags"]) + " " + row["text"]).casefold():
                continue
            if tag and tag not in asset["tags"]:
                continue
            result.append(asset)
        return result

    def update(self, id, *, actor: Principal, expected_revision, title, tags, visibility, shared_with):
        title = _nonblank(title, "title")
        tags = _strings(tags, "tags", maximum=30, item_maximum=80)
        shared_with = _strings(shared_with, "shared_with")
        if visibility not in _VISIBILITIES:
            raise ValueError("invalid visibility")
        expected_revision = _revision(expected_revision)
        with self._connect(write=True) as conn:
            self._write_row(conn, id, actor)
            cursor = conn.execute(
                "UPDATE assets SET title=?,tags_json=?,visibility=?,shared_json=?,revision=revision+1 WHERE id=? AND revision=?",
                (title, _json(tags), visibility, _json(shared_with), id, expected_revision),
            )
            if cursor.rowcount != 1:
                raise ValueError("revision conflict")
            return self._asset(self._row(conn, id))

    def soft_delete(self, id, *, actor: Principal, reason: str):
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("reason is required")
        now = datetime.now(UTC).isoformat()
        with self._connect(write=True) as conn:
            row = self._write_row(conn, id, actor)
            existing = conn.execute("SELECT deleted_at FROM asset_deletions WHERE asset_id=?", (id,)).fetchone()
            if existing is None:
                conn.execute("INSERT INTO asset_deletions(asset_id,tenant_id,deleted_at,deleted_by,reason) VALUES(?,?,?,?,?)", (id, row["tenant_id"], now, actor.subject, reason.strip()[:2000]))
            result = self._asset(row)
            result["deletedAt"] = now if existing is None else existing["deleted_at"]
            result["deletedBy"] = actor.subject
            return result

    def restore(self, id, *, actor: Principal):
        with self._connect(write=True) as conn:
            row = self._row(conn, id)
            if row is None or row["tenant_id"] != actor.tenant_id:
                raise KeyError(id)
            if actor.role != "admin" and row["created_by"] != actor.subject:
                raise PermissionError("asset is not restorable")
            if conn.execute("SELECT 1 FROM asset_deletions WHERE asset_id=?", (id,)).fetchone() is None:
                return self._asset(row)
            conn.execute("DELETE FROM asset_deletions WHERE asset_id=?", (id,))
            return self._asset(row)

    def deleted_candidates(self, *, tenant_id: str, before):
        cutoff = _utc(before, "before").isoformat()
        with self._connect() as conn:
            rows = conn.execute("SELECT d.asset_id,d.tenant_id,d.deleted_at,d.deleted_by,d.reason FROM asset_deletions d WHERE d.tenant_id=? AND d.deleted_at<? ORDER BY d.deleted_at,d.asset_id", (tenant_id, cutoff)).fetchall()
        return [{"resourceType": "asset", "resourceId": row["asset_id"], "tenantId": row["tenant_id"], "createdAt": row["deleted_at"], "deletedBy": row["deleted_by"], "reason": row["reason"]} for row in rows]

    def purge_deleted(self, id, *, tenant_id: str):
        with self._connect(write=True) as conn:
            row = conn.execute("SELECT asset_id FROM asset_deletions WHERE asset_id=? AND tenant_id=?", (id, tenant_id)).fetchone()
            if row is None:
                return False
            # A privileged retention purge is the one explicit exception to
            # immutable version history; triggers are recreated before commit.
            conn.execute("DROP TRIGGER IF EXISTS immutable_asset_version_update")
            conn.execute("DROP TRIGGER IF EXISTS immutable_asset_version_delete")
            conn.execute("DELETE FROM asset_versions WHERE asset_id=?", (id,))
            conn.execute("DELETE FROM assets WHERE id=? AND tenant_id=?", (id, tenant_id))
            conn.execute("CREATE TRIGGER immutable_asset_version_update BEFORE UPDATE ON asset_versions BEGIN SELECT RAISE(ABORT, 'asset versions are immutable'); END")
            conn.execute("CREATE TRIGGER immutable_asset_version_delete BEFORE DELETE ON asset_versions BEGIN SELECT RAISE(ABORT, 'asset versions are immutable'); END")
            return True

    def original(self, id, *, actor: Principal, version=None):
        with self._connect() as conn:
            row = self._visible_row(conn, id, actor)
            if version is not None:
                row = self._version_row(conn, id, version)
            return row["filename"], bytes(row["original"])

    @staticmethod
    def _insert_version(conn, row, author, created_at):
        version = row['current_version']
        conn.execute('INSERT INTO asset_versions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (row['id'], version, version - 1 or None, row['content_hash'], row['filename'],
                      row['original'], created_at, author, row['text'], row['segments_json'],
                      row['metadata_json'], row['warnings_json'], row['method']))

    @staticmethod
    def _version_row(conn, asset_id, version):
        _revision(version)
        row = conn.execute('SELECT * FROM asset_versions WHERE asset_id=? AND version=?', (asset_id, version)).fetchone()
        if row is None:
            raise KeyError(version)
        return row

    @staticmethod
    def _version(row, *, summary=False):
        result = {'assetId': row['asset_id'], 'version': row['version'], 'parentVersion': row['parent_version'],
                  'contentHash': row['content_hash'], 'filename': row['filename'], 'createdAt': row['created_at'],
                  'createdBy': row['created_by'], 'method': row['method'],
                  'metadata': json.loads(row['metadata_json']), 'warnings': json.loads(row['warnings_json'])}
        if not summary:
            result.update(text=row['text'], segments=json.loads(row['segments_json']))
        return result

    def versions(self, id, *, actor: Principal):
        with self._connect() as conn:
            self._visible_row(conn, id, actor)
            rows = conn.execute('SELECT * FROM asset_versions WHERE asset_id=? ORDER BY version DESC', (id,)).fetchall()
            return [self._version(row, summary=True) for row in rows]

    def get_version(self, id, version, *, actor: Principal):
        with self._connect() as conn:
            self._visible_row(conn, id, actor)
            return self._version(self._version_row(conn, id, version))

    def add_version(self, id, filename, data, extracted, *, actor: Principal, expected_revision):
        expected_revision = _revision(expected_revision)
        with self._connect(write=True) as conn:
            row = self._write_row(conn, id, actor)
            if row['revision'] != expected_revision:
                raise ValueError('revision conflict')
            filename, data, document = self._prepare_document(filename, data, extracted)
            conn.execute('UPDATE assets SET filename=?,original=?,text=?,segments_json=?,metadata_json=?,warnings_json=?,method=?,content_hash=?,current_version=current_version+1,revision=revision+1 WHERE id=?',
                         (filename, data, document['text'], _json(document['segments']), _json(document['metadata']),
                          _json(document['warnings']), document['method'], hashlib.sha256(data).hexdigest(), id))
            row = self._row(conn, id)
            self._insert_version(conn, row, actor.subject, datetime.now(UTC).isoformat())
            return self._asset(row)

    def diff(self, id, from_version, to_version, *, actor: Principal, report=None):
        from .asset_diff import compare_versions
        with self._connect() as conn:
            self._visible_row(conn, id, actor)
            before = self._version(self._version_row(conn, id, from_version))
            after = self._version(self._version_row(conn, id, to_version))
            return compare_versions(before, after, report=report)

    @staticmethod
    def _terms(query: str) -> list[str]:
        if not isinstance(query, str):
            raise TypeError("query must be a string")
        terms: list[str] = []
        for raw in re.findall(r"[A-Za-z0-9_]+|[\u3400-\u9fff]+", query.casefold()):
            # Split only common interrogative glue. This is lexical matching,
            # deliberately not a semantic tokenizer or inferred answer model.
            pieces = re.split(r"(?:是什么|为什么|怎么样|怎么办|何时|什么时候|如何|怎么|是否|哪些|哪个)", raw)
            terms.extend(piece for piece in pieces if len(piece) >= 2 or re.fullmatch(r"[A-Za-z0-9_]+", piece))
        return list(dict.fromkeys(terms))

    def search(self, query, *, actor: Principal, limit=20):
        terms = self._terms(query)
        if not terms:
            return []
        if not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        hits = []
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM assets WHERE tenant_id=?", (actor.tenant_id,)).fetchall()
        for row in rows:
            if self._is_deleted(row["id"]):
                continue
            if not self._readable(row, actor):
                continue
            for segment in json.loads(row["segments_json"]):
                haystack = segment["text"].casefold()
                matched = sum(haystack.count(term) for term in terms)
                if matched:
                    hits.append({"assetId": row["id"], "title": row["title"], "segmentId": segment["id"],
                                 "location": segment["location"], "text": segment["text"], "score": float(matched)})
        hits.sort(key=lambda hit: (-hit["score"], hit["assetId"], hit["segmentId"]))
        return hits[:limit]

    def ask(self, question, *, actor: Principal):
        hits = self.search(question, actor=actor, limit=5)
        if not hits:
            return {"answer": _NO_ANSWER, "citations": [], "mode": "extractive"}
        citations = [{key: hit[key] for key in ("assetId", "title", "segmentId", "location", "text")} for hit in hits]
        return {"answer": "\n".join(hit["text"] for hit in hits), "citations": citations, "mode": "extractive"}

    def add_obligation(self, id, *, actor: Principal, expected_revision, title, due_at, kind):
        title = _nonblank(title, "title")
        expected_revision = _revision(expected_revision)
        due = _utc(due_at, "due_at").isoformat()
        if kind not in _OBLIGATION_KINDS:
            raise ValueError("invalid obligation kind")
        obligation_id = uuid.uuid4().hex
        now = datetime.now(UTC).isoformat()
        with self._connect(write=True) as conn:
            self._write_row(conn, id, actor)
            cursor = conn.execute("UPDATE assets SET revision=revision+1 WHERE id=? AND revision=?", (id, expected_revision))
            if cursor.rowcount != 1:
                raise ValueError("revision conflict")
            conn.execute("INSERT INTO obligations VALUES(?,?,?,?,?,?,?,NULL)",
                         (obligation_id, id, title, due, kind, now, actor.subject))
            return self._asset(self._row(conn, id))

    def complete_obligation(self, id, obligation_id, *, actor: Principal, expected_revision):
        expected_revision = _revision(expected_revision)
        now = datetime.now(UTC).isoformat()
        with self._connect(write=True) as conn:
            self._write_row(conn, id, actor)
            obligation = conn.execute("SELECT completed_at FROM obligations WHERE id=? AND asset_id=?", (obligation_id, id)).fetchone()
            if obligation is None:
                raise KeyError(obligation_id)
            if obligation["completed_at"] is not None:
                raise ValueError("obligation already completed")
            cursor = conn.execute("UPDATE assets SET revision=revision+1 WHERE id=? AND revision=?", (id, expected_revision))
            if cursor.rowcount != 1:
                raise ValueError("revision conflict")
            conn.execute("UPDATE obligations SET completed_at=? WHERE id=?", (now, obligation_id))
            return self._asset(self._row(conn, id))

    @staticmethod
    def _obligation(row: sqlite3.Row) -> dict[str, Any]:
        return {"id": row["id"], "assetId": row["asset_id"], "title": row["title"],
                "dueAt": row["due_at"], "kind": row["kind"], "createdAt": row["created_at"],
                "createdBy": row["created_by"], "completedAt": row["completed_at"]}

    def obligations(self, id, *, actor: Principal):
        with self._connect() as conn:
            self._visible_row(conn, id, actor)
            rows = conn.execute("SELECT * FROM obligations WHERE asset_id=? ORDER BY due_at,id", (id,)).fetchall()
            return [self._obligation(row) for row in rows]

    def dispatch_reminders(self, *, actor: Principal, now=None):
        instant = datetime.now(UTC) if now is None else _utc(now, "now")
        created = []
        with self._connect(write=True) as conn:
            rows = conn.execute(
                "SELECT o.id obligation_id,o.asset_id,o.due_at,o.kind,"
                "a.tenant_id,a.created_by,a.visibility,a.shared_json "
                "FROM obligations o JOIN assets a ON a.id=o.asset_id "
                "WHERE a.tenant_id=? AND o.completed_at IS NULL AND o.due_at<=?",
                (actor.tenant_id, instant.isoformat()),
            ).fetchall()
            for row in rows:
                if not self._readable(row, actor):
                    continue
                note_id = uuid.uuid4().hex
                created_at = datetime.now(UTC).isoformat()
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO asset_notifications(id,asset_id,obligation_id,tenant_id,recipient,created_at,read_at) VALUES(?,?,?,?,?,?,NULL)",
                    (note_id, row["asset_id"], row["obligation_id"], actor.tenant_id, actor.subject, created_at),
                )
                if cursor.rowcount:
                    created.append(self._notification(conn.execute("SELECT n.*,o.title obligation_title,o.due_at,o.kind,a.title asset_title FROM asset_notifications n JOIN obligations o ON o.id=n.obligation_id JOIN assets a ON a.id=n.asset_id WHERE n.id=?", (note_id,)).fetchone()))
        return created

    @staticmethod
    def _notification(row: sqlite3.Row) -> dict[str, Any]:
        return {"id": row["id"], "assetId": row["asset_id"], "assetTitle": row["asset_title"],
                "obligationId": row["obligation_id"], "obligationTitle": row["obligation_title"],
                "kind": row["kind"], "dueAt": row["due_at"], "recipient": row["recipient"],
                "createdAt": row["created_at"], "readAt": row["read_at"]}

    def notifications(self, *, actor: Principal):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT n.*,o.title obligation_title,o.due_at,o.kind,a.title asset_title,a.tenant_id,a.created_by,a.visibility,a.shared_json "
                "FROM asset_notifications n JOIN obligations o ON o.id=n.obligation_id JOIN assets a ON a.id=n.asset_id "
                "WHERE n.tenant_id=? AND n.recipient=? ORDER BY n.created_at DESC",
                (actor.tenant_id, actor.subject),
            ).fetchall()
            return [self._notification(row) for row in rows if self._readable(row, actor)]

    def mark_read(self, notification_id, *, actor: Principal):
        with self._connect(write=True) as conn:
            row = conn.execute(
                "SELECT n.*,o.title obligation_title,o.due_at,o.kind,a.title asset_title,a.tenant_id,a.created_by,a.visibility,a.shared_json "
                "FROM asset_notifications n JOIN obligations o ON o.id=n.obligation_id JOIN assets a ON a.id=n.asset_id "
                "WHERE n.id=? AND n.tenant_id=? AND n.recipient=?",
                (notification_id, actor.tenant_id, actor.subject),
            ).fetchone()
            if row is None or not self._readable(row, actor):
                raise KeyError(notification_id)
            if row["read_at"] is None:
                conn.execute("UPDATE asset_notifications SET read_at=? WHERE id=?", (datetime.now(UTC).isoformat(), notification_id))
                row = conn.execute(
                    "SELECT n.*,o.title obligation_title,o.due_at,o.kind,a.title asset_title,a.tenant_id,a.created_by,a.visibility,a.shared_json "
                    "FROM asset_notifications n JOIN obligations o ON o.id=n.obligation_id JOIN assets a ON a.id=n.asset_id WHERE n.id=?",
                    (notification_id,),
                ).fetchone()
            return self._notification(row)
