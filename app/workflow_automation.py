"""Business calendars and data-minimized, signed outbound integration events."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import math
import socket
import sqlite3
import time
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class BusinessCalendar(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    timezone: str = "Asia/Shanghai"
    workdays: tuple[int, ...] = (0, 1, 2, 3, 4)
    work_start: str = "09:00"
    work_end: str = "18:00"
    holidays: tuple[date, ...] = ()

    @model_validator(mode="after")
    def validate_calendar(self):
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("unknown timezone") from exc
        if not self.workdays or any(isinstance(day, bool) or day not in range(7) for day in self.workdays) or len(set(self.workdays)) != len(self.workdays):
            raise ValueError("workdays must contain unique weekdays 0..6")
        start, end = self._clock(self.work_start), self._clock(self.work_end)
        if start >= end:
            raise ValueError("work_start must be before work_end")
        return self

    @staticmethod
    def _clock(value: str) -> time:
        try:
            parsed = time.fromisoformat(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("business hours must use HH:MM") from exc
        if parsed.tzinfo is not None or parsed.second or parsed.microsecond:
            raise ValueError("business hours must use HH:MM")
        return parsed

    def _working(self, day: date) -> bool:
        return day.weekday() in self.workdays and day not in self.holidays

    def _next_open(self, current: datetime) -> datetime:
        start, end = self._clock(self.work_start), self._clock(self.work_end)
        while True:
            if self._working(current.date()):
                opening, closing = datetime.combine(current.date(), start, current.tzinfo), datetime.combine(current.date(), end, current.tzinfo)
                if current < opening:
                    return opening
                if current < closing:
                    return current
            current = datetime.combine(current.date() + timedelta(days=1), start, current.tzinfo)

    def add_business_minutes(self, started_at: datetime, minutes: int) -> datetime:
        if started_at.tzinfo is None or started_at.utcoffset() is None:
            raise ValueError("started_at must be timezone-aware")
        if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes < 0:
            raise ValueError("minutes must be a non-negative integer")
        zone = ZoneInfo(self.timezone)
        current, remaining = self._next_open(started_at.astimezone(zone)), minutes
        closing_time = self._clock(self.work_end)
        while remaining:
            closing = datetime.combine(current.date(), closing_time, zone)
            available = max(0, int((closing - current).total_seconds() // 60))
            consumed = min(remaining, available)
            current += timedelta(minutes=consumed)
            remaining -= consumed
            if remaining:
                current = self._next_open(current + timedelta(minutes=1))
        return current.astimezone(UTC)


class OutboundPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    url: str
    allowed_hosts: tuple[str, ...] = Field(min_length=1)
    secret: str = Field(min_length=8, max_length=4096, repr=False)
    timeout_seconds: float = Field(default=5, gt=0, le=30)

    @model_validator(mode="after")
    def secure_target(self):
        parsed = urlsplit(self.url)
        host = (parsed.hostname or "").rstrip(".").lower()
        allowed = {item.rstrip(".").lower() for item in self.allowed_hosts}
        if parsed.scheme != "https" or parsed.username or parsed.password or not host or parsed.port not in {None, 443} or host not in allowed:
            raise ValueError("outbound URL must be an allowlisted HTTPS endpoint")
        if host == "localhost" or host.endswith(".localhost"):
            raise ValueError("local outbound targets are forbidden")
        try:
            if not ipaddress.ip_address(host).is_global:
                raise ValueError("private outbound targets are forbidden")
        except ValueError as exc:
            if "private outbound" in str(exc):
                raise
        if not math.isfinite(self.timeout_seconds):
            raise ValueError("timeout must be finite")
        return self


def _assert_public_resolution(host: str, port: int) -> None:
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise RuntimeError("outbound host cannot be resolved") from exc
    if not addresses:
        raise RuntimeError("outbound host cannot be resolved")
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address[4][0])
        except ValueError as exc:
            raise RuntimeError("outbound host returned an invalid address") from exc
        if not ip.is_global:
            raise RuntimeError("outbound host resolved to a non-public address")


IntegrationKind = Literal["generic", "enterprise_im", "ticket", "procurement_crm", "electronic_signature"]


def build_integration_event(event_type: str, *, tenant_id: str, resource_id: str, summary: str, integration_kind: IntegrationKind = "generic") -> dict:
    values = [event_type, tenant_id, resource_id, summary]
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("integration event fields must be nonblank")
    if len(summary.strip()) > 500:
        raise ValueError("summary exceeds 500 characters")
    if integration_kind not in {"generic", "enterprise_im", "ticket", "procurement_crm", "electronic_signature"}:
        raise ValueError("unknown integration kind")
    return {"type": event_type.strip(), "tenantId": tenant_id.strip(), "resourceId": resource_id.strip(),
            "summary": summary.strip(), "integrationKind": integration_kind,
            "createdAt": datetime.now(UTC).isoformat()}


class SignedWebhook:
    def __init__(self, policy: OutboundPolicy):
        self.policy = policy

    def send(self, event: dict, *, idempotency_key: str) -> dict:
        if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 200:
            raise ValueError("idempotency_key is required")
        permitted = {"type", "tenantId", "resourceId", "summary", "integrationKind", "createdAt"}
        if not isinstance(event, dict) or not set(event).issubset(permitted) or not {"type", "tenantId", "resourceId", "summary"}.issubset(event):
            raise ValueError("integration event contains unsupported data")
        payload = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        signature = hmac.new(self.policy.secret.encode(), payload, hashlib.sha256).hexdigest()
        parsed = urlsplit(self.policy.url)
        _assert_public_resolution(parsed.hostname or "", parsed.port or 443)
        with httpx.Client(timeout=self.policy.timeout_seconds, follow_redirects=False) as client:
            response = client.post(self.policy.url, content=payload, headers={
                "Content-Type": "application/json", "Idempotency-Key": idempotency_key.strip(),
                "X-CRA-Signature": f"sha256={signature}",
            })
            response.raise_for_status()
        return {"delivered": True, "status": response.status_code, "idempotencyKey": idempotency_key.strip()}


class IntegrationDispatcher:
    """Persist delivery metadata, retry transient failures and expose alerts."""
    def __init__(self, path, senders: dict[str, object], *, max_attempts: int = 1, retry_backoff_seconds: float = 0.25):
        from pathlib import Path
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 5:
            raise ValueError("max_attempts must be between 1 and 5")
        if not isinstance(retry_backoff_seconds, (int, float)) or not math.isfinite(retry_backoff_seconds) or retry_backoff_seconds < 0 or retry_backoff_seconds > 30:
            raise ValueError("retry_backoff_seconds must be between 0 and 30")
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.senders = dict(senders)
        self.max_attempts = max_attempts
        self.retry_backoff_seconds = float(retry_backoff_seconds)
        with sqlite3.connect(self.path) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS integration_deliveries(
                idempotency_key TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, integration_kind TEXT NOT NULL,
                event_type TEXT NOT NULL, resource_id TEXT NOT NULL, attempted_at TEXT NOT NULL,
                delivered INTEGER NOT NULL, status INTEGER, error_code TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 1, alert INTEGER NOT NULL DEFAULT 0)""")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(integration_deliveries)").fetchall()}
            if "attempt_count" not in columns:
                conn.execute("ALTER TABLE integration_deliveries ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 1")
            if "alert" not in columns:
                conn.execute("ALTER TABLE integration_deliveries ADD COLUMN alert INTEGER NOT NULL DEFAULT 0")

    def dispatch(self, event: dict, *, idempotency_key: str) -> dict:
        sender = self.senders.get(event.get("integrationKind"))
        if sender is None:
            return {"delivered": False, "configured": False, "idempotencyKey": idempotency_key}
        with sqlite3.connect(self.path, timeout=10) as conn:
            previous = conn.execute("SELECT delivered,status FROM integration_deliveries WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if previous is not None:
                return {"delivered": bool(previous[0]), "status": previous[1], "idempotencyKey": idempotency_key, "duplicate": True}
        delivered, status, error_code, attempt_count = False, None, None, 0
        for attempt in range(1, self.max_attempts + 1):
            attempt_count = attempt
            try:
                result = sender.send(event, idempotency_key=idempotency_key)
                delivered, status, error_code = bool(result.get("delivered")), result.get("status"), None
            except (httpx.HTTPError, RuntimeError) as exc:
                delivered, status, error_code = False, None, type(exc).__name__
            if delivered:
                break
            if attempt < self.max_attempts and self.retry_backoff_seconds:
                time.sleep(self.retry_backoff_seconds * (2 ** (attempt - 1)))
        alert = not delivered
        with sqlite3.connect(self.path, timeout=10) as conn:
            conn.execute("INSERT OR IGNORE INTO integration_deliveries VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                idempotency_key, event["tenantId"], event["integrationKind"], event["type"], event["resourceId"],
                datetime.now(UTC).isoformat(), int(delivered), status, error_code, attempt_count, int(alert),
            ))
        response = {"delivered": delivered, "status": status, "idempotencyKey": idempotency_key,
                    **({"errorCode": error_code} if error_code else {})}
        if self.max_attempts > 1 or alert:
            response.update({"attemptCount": attempt_count, "alert": alert})
        return response

    def attempts(self, *, tenant_id: str) -> list[dict]:
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute("SELECT idempotency_key,integration_kind,event_type,resource_id,attempted_at,delivered,status,error_code,attempt_count,alert FROM integration_deliveries WHERE tenant_id=? ORDER BY attempted_at DESC", (tenant_id,)).fetchall()
        return [{"idempotencyKey": row[0], "integrationKind": row[1], "eventType": row[2], "resourceId": row[3],
                 "attemptedAt": row[4], "delivered": bool(row[5]), "status": row[6], "errorCode": row[7],
                 "attemptCount": row[8], "alert": bool(row[9])} for row in rows]


class IntegrationConfigStore:
    """Tenant-scoped adapter configuration and safe application dispatch."""

    _KINDS = {"generic", "enterprise_im", "ticket", "procurement_crm", "electronic_signature"}

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS integration_configs(
                tenant_id TEXT NOT NULL, integration_kind TEXT NOT NULL, url TEXT NOT NULL,
                allowed_hosts_json TEXT NOT NULL, secret TEXT NOT NULL,
                timeout_seconds REAL NOT NULL, enabled INTEGER NOT NULL,
                updated_at TEXT NOT NULL, PRIMARY KEY(tenant_id, integration_kind))""")
        IntegrationDispatcher(self.path, {})

    @staticmethod
    def _dispatcher(path: Path, kind: str, sender: SignedWebhook) -> IntegrationDispatcher:
        return IntegrationDispatcher(path, {kind: sender}, max_attempts=3, retry_backoff_seconds=0.25)

    @classmethod
    def _kind(cls, value: str) -> str:
        if value not in cls._KINDS:
            raise ValueError("unknown integration kind")
        return value

    @staticmethod
    def _tenant(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("tenant_id must not be blank")
        return value.strip()

    def configure(self, *, tenant_id: str, integration_kind: IntegrationKind, url: str,
                  allowed_hosts: list[str] | tuple[str, ...], secret: str | None,
                  enabled: bool = False, timeout_seconds: float = 5.0) -> dict[str, Any]:
        tenant_id = self._tenant(tenant_id)
        kind = self._kind(integration_kind)
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be boolean")
        with sqlite3.connect(self.path) as conn:
            previous = conn.execute("SELECT secret FROM integration_configs WHERE tenant_id=? AND integration_kind=?", (tenant_id, kind)).fetchone()
            if secret is None or not str(secret).strip():
                if previous is None:
                    raise ValueError("secret is required for a new integration")
                secret = previous[0]
            policy = OutboundPolicy(url=url, allowed_hosts=tuple(allowed_hosts), secret=str(secret), timeout_seconds=timeout_seconds)
            now = datetime.now(UTC).isoformat()
            conn.execute("""INSERT INTO integration_configs(tenant_id,integration_kind,url,allowed_hosts_json,secret,timeout_seconds,enabled,updated_at)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(tenant_id,integration_kind) DO UPDATE SET
                url=excluded.url, allowed_hosts_json=excluded.allowed_hosts_json, secret=excluded.secret,
                timeout_seconds=excluded.timeout_seconds, enabled=excluded.enabled, updated_at=excluded.updated_at""",
                (tenant_id, kind, policy.url, json.dumps(list(policy.allowed_hosts), ensure_ascii=False), policy.secret, policy.timeout_seconds, int(enabled), now))
        return self._public_row((tenant_id, kind, policy.url, json.dumps(list(policy.allowed_hosts)), policy.timeout_seconds, int(enabled), now))

    @staticmethod
    def _public_row(row) -> dict[str, Any]:
        return {"integrationKind": row[1], "url": row[2], "allowedHosts": json.loads(row[3]),
                "timeoutSeconds": float(row[4]), "enabled": bool(row[5]), "hasSecret": True, "updatedAt": row[6]}

    def list_configs(self, *, tenant_id: str) -> list[dict[str, Any]]:
        tenant_id = self._tenant(tenant_id)
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute("SELECT tenant_id,integration_kind,url,allowed_hosts_json,timeout_seconds,enabled,updated_at FROM integration_configs WHERE tenant_id=? ORDER BY integration_kind", (tenant_id,)).fetchall()
        return [self._public_row(row) for row in rows]

    def delete(self, *, tenant_id: str, integration_kind: IntegrationKind) -> bool:
        with sqlite3.connect(self.path) as conn:
            cursor = conn.execute("DELETE FROM integration_configs WHERE tenant_id=? AND integration_kind=?", (self._tenant(tenant_id), self._kind(integration_kind)))
        return cursor.rowcount == 1

    def dispatch(self, event: dict, *, idempotency_key: str) -> dict[str, Any]:
        tenant_id = self._tenant(event.get("tenantId"))
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute("SELECT integration_kind,url,allowed_hosts_json,secret,timeout_seconds FROM integration_configs WHERE tenant_id=? AND enabled=1", (tenant_id,)).fetchall()
        if not rows:
            return {"delivered": False, "configured": False, "idempotencyKey": idempotency_key}
        results = []
        for kind, url, hosts_json, secret, timeout in rows:
            policy = OutboundPolicy(url=url, allowed_hosts=tuple(json.loads(hosts_json)), secret=secret, timeout_seconds=timeout)
            sender = SignedWebhook(policy)
            outbound = {**event, "integrationKind": kind}
            dispatcher = self._dispatcher(self.path, kind, sender)
            results.append(dispatcher.dispatch(outbound, idempotency_key=f"{idempotency_key}:{kind}"))
        return {"delivered": any(result.get("delivered") for result in results), "configured": True,
                "results": results, "idempotencyKey": idempotency_key}

    def attempts(self, *, tenant_id: str) -> list[dict[str, Any]]:
        """Return delivery metadata and alert flags without exposing secrets."""
        tenant_id = self._tenant(tenant_id)
        with sqlite3.connect(self.path) as conn:
            rows = conn.execute("SELECT idempotency_key,integration_kind,event_type,resource_id,attempted_at,delivered,status,error_code,attempt_count,alert FROM integration_deliveries WHERE tenant_id=? ORDER BY attempted_at DESC", (tenant_id,)).fetchall()
        return [{"idempotencyKey": row[0], "integrationKind": row[1], "eventType": row[2], "resourceId": row[3],
                 "attemptedAt": row[4], "delivered": bool(row[5]), "status": row[6], "errorCode": row[7],
                 "attemptCount": row[8], "alert": bool(row[9])} for row in rows]
