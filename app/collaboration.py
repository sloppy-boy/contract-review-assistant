"""Tenant-scoped review collaboration with atomic events and notifications."""
from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3
from typing import Annotated, Callable
import uuid

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .security import Principal


class RequestContent(BaseModel):
    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    title: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    contractType: Annotated[str, StringConstraints(min_length=1)]
    description: str = Field(default="", max_length=10000)


WRITERS = {"admin", "legal_reviewer", "requester"}
REVIEWERS = {"admin", "legal_reviewer"}
TERMINAL = {"approved", "cancelled"}


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _nonblank(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be blank")
    return value.strip()


def _subjects(values) -> list[str]:
    return list(dict.fromkeys(_nonblank(value, "subject") for value in values))


class CollaborationStore:
    def __init__(self, path: Path | str, *, clock: Callable[[], datetime] | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.clock = clock or (lambda: datetime.now(UTC))
        with closing(self._connect()) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS collaboration_requests (
                    id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, data_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS collaboration_requests_tenant
                    ON collaboration_requests(tenant_id);
                CREATE TABLE IF NOT EXISTS collaboration_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL,
                    type TEXT NOT NULL, subject TEXT NOT NULL, created_at TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    FOREIGN KEY(request_id) REFERENCES collaboration_requests(id)
                );
                CREATE INDEX IF NOT EXISTS collaboration_events_request
                    ON collaboration_events(request_id, id);
                CREATE TABLE IF NOT EXISTS collaboration_notifications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL, recipient TEXT NOT NULL, type TEXT NOT NULL,
                    created_at TEXT NOT NULL, read_at TEXT, data_json TEXT NOT NULL,
                    FOREIGN KEY(request_id) REFERENCES collaboration_requests(id)
                );
                CREATE INDEX IF NOT EXISTS collaboration_notifications_recipient
                    ON collaboration_notifications(tenant_id, recipient, id);
                CREATE UNIQUE INDEX IF NOT EXISTS collaboration_sla_notification_once
                    ON collaboration_notifications(request_id, recipient, type)
                    WHERE type LIKE 'sla_%';
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextmanager
    def _transaction(self):
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            with conn:
                yield conn

    def _now(self) -> datetime:
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("clock must return an aware datetime")
        return now.astimezone(UTC)

    @staticmethod
    def _load(conn, request_id: str, actor: Principal) -> dict:
        row = conn.execute("SELECT data_json FROM collaboration_requests WHERE id=? AND tenant_id=?",
                           (request_id, actor.tenant_id)).fetchone()
        if row is None:
            raise KeyError(request_id)
        return json.loads(row["data_json"])

    def _public(self, request: dict, *, summary=False) -> dict:
        result = {key: value for key, value in request.items() if not key.startswith("_")}
        result["isOverdue"] = bool(result["dueAt"] and result["status"] not in TERMINAL
                                   and datetime.fromisoformat(result["dueAt"]) < self._now())
        if summary:
            result.pop("approvalSnapshot", None)
        return result

    @staticmethod
    def _require_role(actor, roles=WRITERS):
        if actor.role not in roles:
            raise PermissionError("insufficient role")

    @classmethod
    def _require_owner(cls, request, actor):
        cls._require_role(actor)
        if actor.subject != request["createdBy"] and actor.role != "admin":
            raise PermissionError("only the creator or admin may perform this action")

    @classmethod
    def _require_reviewer(cls, request, actor):
        cls._require_role(actor, REVIEWERS)
        if actor.subject != request["assignee"] and actor.role != "admin":
            raise PermissionError("only the assignee or admin may perform this action")

    @staticmethod
    def _require_state(request, *statuses):
        if request["status"] not in statuses:
            raise ValueError("action is not allowed in the current state")

    @staticmethod
    def _validate_run(request: dict, run: dict):
        if (not isinstance(run, dict) or not run.get("id") or run.get("tenantId") != request["tenantId"]
                or run.get("contract_type", run.get("contractType")) != request["contractType"]
                or run.get("status") != "done" or not isinstance(run.get("report"), dict)):
            raise ValueError("review run must be completed with a report in the same tenant and contract type")

    def _append(self, conn, request, actor, kind, data, recipients, now):
        event_data = {"status": request["status"], "revision": request["revision"], **data}
        cursor = conn.execute(
            "INSERT INTO collaboration_events(request_id,type,subject,created_at,data_json) VALUES(?,?,?,?,?)",
            (request["id"], kind, actor.subject, now, _json(event_data)),
        )
        summary = {"eventId": cursor.lastrowid, "subject": actor.subject, "title": request["title"],
                   "data": {key: value for key, value in event_data.items() if key != "approvalSnapshot"}}
        for recipient in _subjects(value for value in recipients if value is not None):
            if recipient == actor.subject:
                continue
            conn.execute(
                """INSERT INTO collaboration_notifications
                   (request_id,tenant_id,recipient,type,created_at,data_json) VALUES(?,?,?,?,?,?)""",
                (request["id"], request["tenantId"], recipient, kind, now, _json(summary)),
            )

    def _change(self, request_id, *, actor, expected_revision, action) -> dict:
        with self._transaction() as conn:
            request = self._load(conn, request_id, actor)
            if type(expected_revision) is not int or request["revision"] != expected_revision:
                raise ValueError("request revision conflict; reload before retrying")
            now = self._now()
            kind, data, recipients = action(request, now)
            request["revision"] += 1
            request["updatedAt"] = now.isoformat()
            conn.execute("UPDATE collaboration_requests SET data_json=? WHERE id=?",
                         (_json(request), request_id))
            self._append(conn, request, actor, kind, data, recipients, now.isoformat())
        return self._public(request)

    def create(self, content: RequestContent, *, actor: Principal) -> dict:
        self._require_role(actor)
        raw_content = content.model_dump(mode="python") if isinstance(content, RequestContent) else content
        content = RequestContent.model_validate(raw_content)
        now = self._now().isoformat()
        request = {"id": uuid.uuid4().hex, "tenantId": actor.tenant_id, **content.model_dump(),
                   "status": "draft", "createdBy": actor.subject, "createdAt": now, "updatedAt": now,
                   "revision": 1, "assignee": None, "approver": None, "dueAt": None,
                   "linkedRunId": None, "approvalRound": 0, "approvalSnapshot": None}
        with self._transaction() as conn:
            conn.execute("INSERT INTO collaboration_requests(id,tenant_id,data_json) VALUES(?,?,?)",
                         (request["id"], actor.tenant_id, _json(request)))
            self._append(conn, request, actor, "created", {}, (), now)
        return self._public(request)

    def get(self, request_id: str, *, actor: Principal) -> dict | None:
        with closing(self._connect()) as conn:
            try:
                return self._public(self._load(conn, request_id, actor))
            except KeyError:
                return None

    def list_requests(self, *, actor: Principal, status: str | None = None) -> list[dict]:
        with closing(self._connect()) as conn:
            rows = conn.execute("SELECT data_json FROM collaboration_requests WHERE tenant_id=? ORDER BY rowid DESC",
                                (actor.tenant_id,)).fetchall()
        requests = [json.loads(row["data_json"]) for row in rows]
        return [self._public(request, summary=True) for request in requests if status is None or request["status"] == status]

    def submit(self, request_id: str, *, actor: Principal, expected_revision: int, recipients=()) -> dict:
        def action(request, now):
            self._require_owner(request, actor)
            self._require_state(request, "draft")
            request["status"] = "submitted"
            return "submitted", {}, recipients
        return self._change(request_id, actor=actor, expected_revision=expected_revision, action=action)

    def assign(self, request_id: str, *, actor: Principal, expected_revision: int,
               assignee: str, approver: str, due_at: str) -> dict:
        def action(request, now):
            self._require_owner(request, actor)
            self._require_state(request, "submitted", "in_review", "changes_requested")
            deadline = datetime.fromisoformat(due_at)
            if deadline.tzinfo is None or deadline.utcoffset() is None or deadline <= now:
                raise ValueError("deadline must be an aware datetime in the future")
            request.update(status="in_review", assignee=_nonblank(assignee, "assignee"),
                           approver=_nonblank(approver, "approver"), dueAt=deadline.astimezone(UTC).isoformat())
            return "assigned", {key: request[key] for key in ("assignee", "approver", "dueAt")}, [assignee, request["createdBy"]]
        return self._change(request_id, actor=actor, expected_revision=expected_revision, action=action)

    def bind_run(self, request_id: str, *, actor: Principal, expected_revision: int, run: dict) -> dict:
        def action(request, now):
            self._require_reviewer(request, actor)
            self._require_state(request, "in_review", "changes_requested")
            self._validate_run(request, run)
            request["linkedRunId"] = run["id"]
            return "run_bound", {"runId": run["id"]}, []
        return self._change(request_id, actor=actor, expected_revision=expected_revision, action=action)

    def request_approval(self, request_id: str, *, actor: Principal, expected_revision: int,
                         run: dict, dispositions: dict) -> dict:
        def action(request, now):
            self._require_reviewer(request, actor)
            self._require_state(request, "in_review", "changes_requested")
            self._validate_run(request, run)
            if not request["approver"] or request["linkedRunId"] != run["id"]:
                raise ValueError("approval requires an approver and the linked completed report")
            if not isinstance(dispositions, dict):
                raise ValueError("dispositions must be an object")
            request.update(status="pending_approval", approvalRound=request["approvalRound"] + 1,
                           approvalSnapshot=json.loads(_json({"runId": run["id"], "report": run["report"],
                                                              "dispositions": dispositions})))
            return "approval_requested", {"approvalRound": request["approvalRound"],
                                           "approvalSnapshot": request["approvalSnapshot"]}, [request["approver"]]
        return self._change(request_id, actor=actor, expected_revision=expected_revision, action=action)

    def decide(self, request_id: str, *, actor: Principal, expected_revision: int,
               decision: str, reason: str) -> dict:
        def action(request, now):
            self._require_role(actor, REVIEWERS)
            if actor.subject != request["approver"]:
                raise PermissionError("only the designated approver may decide")
            self._require_state(request, "pending_approval")
            if decision not in {"approved", "changes_requested"}:
                raise ValueError("invalid approval decision")
            reason_text = _nonblank(reason, "reason")
            request["status"] = decision
            return decision, {"reason": reason_text, "approvalRound": request["approvalRound"]}, [request["createdBy"], request["assignee"]]
        return self._change(request_id, actor=actor, expected_revision=expected_revision, action=action)

    def cancel(self, request_id: str, *, actor: Principal, expected_revision: int, reason: str) -> dict:
        def action(request, now):
            self._require_owner(request, actor)
            if request["status"] in TERMINAL:
                raise ValueError("finalized requests cannot be cancelled")
            reason_text = _nonblank(reason, "reason")
            request["status"] = "cancelled"
            return "cancelled", {"reason": reason_text}, [request["createdBy"], request["assignee"]]
        return self._change(request_id, actor=actor, expected_revision=expected_revision, action=action)

    def comment(self, request_id: str, *, actor: Principal, expected_revision: int,
                body: str, mentions: list[str]) -> dict:
        def action(request, now):
            self._require_role(actor)
            text = _nonblank(body, "comment")
            subjects = _subjects(mentions)
            return "commented", {"body": text, "mentions": subjects}, [request["createdBy"], request["assignee"], request["approver"], *subjects]
        return self._change(request_id, actor=actor, expected_revision=expected_revision, action=action)

    def events(self, request_id: str, *, actor: Principal) -> list[dict]:
        with closing(self._connect()) as conn:
            self._load(conn, request_id, actor)
            rows = conn.execute("SELECT * FROM collaboration_events WHERE request_id=? ORDER BY id", (request_id,)).fetchall()
        return [{"id": row["id"], "type": row["type"], "subject": row["subject"],
                 "createdAt": row["created_at"], "data": json.loads(row["data_json"])} for row in rows]

    def retention_candidates(self, *, tenant_id: str, before) -> list[dict]:
        """Return terminal request identifiers eligible for retention purge."""
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
            rows = conn.execute("SELECT data_json FROM collaboration_requests WHERE tenant_id=?", (tenant_id,)).fetchall()
        candidates = []
        for row in rows:
            request = json.loads(row["data_json"])
            if request.get("status") not in TERMINAL:
                continue
            created_at = datetime.fromisoformat(request["createdAt"]).astimezone(UTC)
            if created_at < cutoff:
                candidates.append({"resourceType": "collaboration_request", "resourceId": request["id"], "tenantId": tenant_id, "createdAt": request["createdAt"], "status": request["status"]})
        return sorted(candidates, key=lambda item: (item["createdAt"], item["resourceId"]))

    def purge_request(self, request_id: str, *, tenant_id: str) -> bool:
        """Delete terminal request metadata and its child audit rows."""
        with self._transaction() as conn:
            row = conn.execute("SELECT data_json FROM collaboration_requests WHERE id=? AND tenant_id=?", (request_id, tenant_id)).fetchone()
            if row is None or json.loads(row["data_json"]).get("status") not in TERMINAL:
                return False
            conn.execute("DELETE FROM collaboration_notifications WHERE request_id=? AND tenant_id=?", (request_id, tenant_id))
            conn.execute("DELETE FROM collaboration_events WHERE request_id=?", (request_id,))
            conn.execute("DELETE FROM collaboration_requests WHERE id=? AND tenant_id=?", (request_id, tenant_id))
            return True

    @staticmethod
    def _notification(row) -> dict:
        return {"id": row["id"], "requestId": row["request_id"], "type": row["type"],
                "recipient": row["recipient"], "createdAt": row["created_at"], "readAt": row["read_at"], **json.loads(row["data_json"])}

    def dispatch_sla_reminders(self, *, tenant_id: str, admin_subjects=(), now=None,
                               pre_due_hours: int = 24, escalation_hours: int = 24) -> list[dict]:
        """Append each request/recipient/SLA level once without changing request revision."""
        instant = self._now() if now is None else now
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        instant = instant.astimezone(UTC)
        if type(pre_due_hours) is not int or type(escalation_hours) is not int or pre_due_hours < 0 or escalation_hours < 0:
            raise ValueError("SLA thresholds must be non-negative integer hours")
        created = []
        with self._transaction() as conn:
            rows = conn.execute("SELECT data_json FROM collaboration_requests WHERE tenant_id=?", (tenant_id,)).fetchall()
            for row in rows:
                request = json.loads(row["data_json"])
                if request["status"] in TERMINAL or not request.get("dueAt"):
                    continue
                due = datetime.fromisoformat(request["dueAt"]).astimezone(UTC)
                if instant >= due + timedelta(hours=escalation_hours):
                    kind, recipients = "sla_overdue_escalation", [request.get("createdBy"), request.get("approver"), *admin_subjects]
                elif instant >= due:
                    kind, recipients = "sla_due", [request.get("assignee"), request.get("approver")]
                elif instant >= due - timedelta(hours=pre_due_hours):
                    kind, recipients = "sla_pre_due", [request.get("assignee"), request.get("approver")]
                else:
                    continue
                inserted = []
                data = {"subject": "system", "title": request["title"], "data": {"dueAt": request["dueAt"], "status": request["status"]}}
                for recipient in _subjects(value for value in recipients if value):
                    cursor = conn.execute(
                        "INSERT OR IGNORE INTO collaboration_notifications(request_id,tenant_id,recipient,type,created_at,data_json) VALUES(?,?,?,?,?,?)",
                        (request["id"], tenant_id, recipient, kind, instant.isoformat(), _json(data)),
                    )
                    if cursor.rowcount:
                        inserted.append(cursor.lastrowid)
                if inserted:
                    conn.execute("INSERT INTO collaboration_events(request_id,type,subject,created_at,data_json) VALUES(?,?,?,?,?)",
                                 (request["id"], kind, "system", instant.isoformat(), _json({"dueAt": request["dueAt"], "recipients": len(inserted)})))
                    for notification_id in inserted:
                        notification = conn.execute("SELECT * FROM collaboration_notifications WHERE id=?", (notification_id,)).fetchone()
                        created.append(self._notification(notification))
        return created

    def notifications(self, *, actor: Principal, unread_only: bool = False) -> list[dict]:
        query = "SELECT * FROM collaboration_notifications WHERE tenant_id=? AND recipient=?"
        if unread_only:
            query += " AND read_at IS NULL"
        with closing(self._connect()) as conn:
            rows = conn.execute(query + " ORDER BY id DESC", (actor.tenant_id, actor.subject)).fetchall()
        return [self._notification(row) for row in rows]

    def mark_read(self, notification_id, *, actor: Principal) -> dict:
        with self._transaction() as conn:
            parameters = (notification_id, actor.tenant_id, actor.subject)
            row = conn.execute("SELECT * FROM collaboration_notifications WHERE id=? AND tenant_id=? AND recipient=?", parameters).fetchone()
            if row is None:
                raise KeyError(notification_id)
            if row["read_at"] is None:
                conn.execute("UPDATE collaboration_notifications SET read_at=? WHERE id=? AND tenant_id=? AND recipient=?",
                             (self._now().isoformat(), *parameters))
                row = conn.execute("SELECT * FROM collaboration_notifications WHERE id=? AND tenant_id=? AND recipient=?", parameters).fetchone()
        return self._notification(row)


__all__ = ["RequestContent", "CollaborationStore"]
