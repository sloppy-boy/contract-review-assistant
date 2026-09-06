"""API-key 身份认证与租户角色边界。"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant_id: str
    role: str


class ApiKeyAuthenticator:
    def __init__(self, *, environment: str, keys: dict[str, dict[str, str]]):
        self.environment = environment
        self.keys = keys

    @classmethod
    def from_environment(cls) -> "ApiKeyAuthenticator":
        raw = os.environ.get("CRA_API_KEYS", "{}")
        try:
            keys = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("CRA_API_KEYS 必须为 JSON 对象") from exc
        return cls(environment=os.environ.get("APP_ENV", "development"), keys=keys)

    def authenticate(self, api_key: str | None) -> Principal:
        if self.environment != "production":
            return Principal(subject="local-developer", tenant_id="local", role="admin")
        payload = self.keys.get(api_key or "")
        if not payload:
            raise PermissionError("authentication required")
        return Principal(
            subject=str(payload["subject"]), tenant_id=str(payload["tenantId"]), role=str(payload["role"]),
        )

    @staticmethod
    def require_role(principal: Principal, *roles: str) -> None:
        if principal.role not in roles:
            raise PermissionError("insufficient role")
