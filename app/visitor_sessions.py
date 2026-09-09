"""Stateless, signed sessions for isolated public visitors."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid

from .security import Principal


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class VisitorSessionManager:
    def __init__(self, secret: str, ttl_seconds: int = 604800):
        if not secret:
            raise ValueError("visitor signing secret is required")
        self._secret = secret.encode("utf-8")
        self.ttl_seconds = ttl_seconds

    def issue(self, *, now: int | None = None) -> tuple[str, Principal]:
        issued_at = int(time.time() if now is None else now)
        identifier = uuid.uuid4().hex
        principal = Principal(subject=f"visitor-{identifier}", tenant_id=f"visitor-{identifier}", role="visitor")
        payload = _encode(json.dumps({
            "sub": principal.subject,
            "tenant": principal.tenant_id,
            "role": principal.role,
            "iat": issued_at,
            "exp": issued_at + self.ttl_seconds,
        }, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        signature = _encode(hmac.new(self._secret, payload.encode("ascii"), hashlib.sha256).digest())
        return f"{payload}.{signature}", principal

    def authenticate(self, token: str, *, now: int | None = None) -> Principal:
        try:
            payload, supplied_signature = token.split(".", 1)
            expected = _encode(hmac.new(self._secret, payload.encode("ascii"), hashlib.sha256).digest())
            if not hmac.compare_digest(supplied_signature, expected):
                raise PermissionError("invalid visitor session")
            data = json.loads(_decode(payload))
        except PermissionError:
            raise
        except Exception as exc:
            raise PermissionError("invalid visitor session") from exc
        current = int(time.time() if now is None else now)
        if current > int(data["exp"]):
            raise PermissionError("visitor session expired")
        if data.get("role") != "visitor" or not str(data.get("tenant", "")).startswith("visitor-"):
            raise PermissionError("invalid visitor session")
        return Principal(subject=str(data["sub"]), tenant_id=str(data["tenant"]), role="visitor")
