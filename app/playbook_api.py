"""Authenticated Playbook lifecycle endpoints, separate from review execution."""
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import BaseModel, ConfigDict, Field

from .playbook_store import PlaybookStore
from .playbooks import PlaybookContent
from .security import ApiKeyAuthenticator, Principal


class NewVersionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sourceVersion: int = Field(gt=0, strict=True)


def _mutate(principal: Principal, operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        ApiKeyAuthenticator.require_role(principal, "admin", "legal_reviewer")
        return operation()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="playbook version not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def create_playbook_router(
    get_store: Callable[[], PlaybookStore], principal_dependency: Callable[..., Principal]
) -> APIRouter:
    router = APIRouter(prefix="/playbooks", tags=["Playbooks"])

    @router.post("", status_code=201)
    def create(content: PlaybookContent, principal: Principal = Depends(principal_dependency)) -> dict:
        return _mutate(principal, lambda: get_store().create_draft(content, tenant_id=principal.tenant_id, subject=principal.subject))

    @router.get("")
    def list_books(activeOnly: bool = False, principal: Principal = Depends(principal_dependency)) -> dict:
        return {"playbooks": get_store().list_playbooks(tenant_id=principal.tenant_id, active_only=activeOnly)}

    @router.get("/{playbook_id}/versions")
    def list_versions(playbook_id: str, principal: Principal = Depends(principal_dependency)) -> dict:
        versions = get_store().list_versions(playbook_id, tenant_id=principal.tenant_id)
        if not versions:
            raise HTTPException(status_code=404, detail="playbook not found")
        return {"versions": versions}

    @router.get("/{playbook_id}/events")
    def list_events(playbook_id: str, principal: Principal = Depends(principal_dependency)) -> dict:
        events = get_store().list_events(playbook_id, tenant_id=principal.tenant_id)
        if not events:
            raise HTTPException(status_code=404, detail="playbook not found")
        return {"events": events}

    @router.get("/{playbook_id}/versions/{version}")
    def get_version(playbook_id: str, version: Annotated[int, Path(gt=0)], principal: Principal = Depends(principal_dependency)) -> dict:
        result = get_store().get_version(playbook_id, version, tenant_id=principal.tenant_id)
        if result is None:
            raise HTTPException(status_code=404, detail="playbook version not found")
        return result

    @router.post("/{playbook_id}/versions", status_code=201)
    def create_version(playbook_id: str, req: NewVersionRequest, principal: Principal = Depends(principal_dependency)) -> dict:
        return _mutate(principal, lambda: get_store().create_version(playbook_id, source_version=req.sourceVersion, tenant_id=principal.tenant_id, subject=principal.subject))

    @router.put("/{playbook_id}/versions/{version}")
    def update(playbook_id: str, version: Annotated[int, Path(gt=0)], content: PlaybookContent, principal: Principal = Depends(principal_dependency)) -> dict:
        return _mutate(principal, lambda: get_store().update_draft(playbook_id, version, content, tenant_id=principal.tenant_id, subject=principal.subject))

    @router.post("/{playbook_id}/versions/{version}/publish")
    def publish(playbook_id: str, version: Annotated[int, Path(gt=0)], principal: Principal = Depends(principal_dependency)) -> dict:
        return _mutate(principal, lambda: get_store().publish(playbook_id, version, tenant_id=principal.tenant_id, subject=principal.subject))

    @router.post("/{playbook_id}/versions/{version}/archive")
    def archive(playbook_id: str, version: Annotated[int, Path(gt=0)], principal: Principal = Depends(principal_dependency)) -> dict:
        return _mutate(principal, lambda: get_store().archive(playbook_id, version, tenant_id=principal.tenant_id, subject=principal.subject))

    return router
