"""Authenticated contract import, repository and obligation endpoints."""
from __future__ import annotations

import hashlib
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .document_import import extract_document
from .asset_reminders import reminder_lifespan
from .security import Principal
from .http_errors import call


class Revision(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expectedRevision: int = Field(strict=True, gt=0)


class AssetUpdate(Revision):
    title: str = Field(min_length=1, max_length=200)
    tags: list[str] = Field(max_length=30)
    visibility: Literal["private", "tenant"]
    sharedWith: list[str] = Field(default_factory=list, max_length=100)


class ObligationCreate(Revision):
    title: str = Field(min_length=1, max_length=200)
    dueAt: str = Field(min_length=1, max_length=60)
    kind: Literal["obligation", "renewal", "key_date"]


class Question(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question: str = Field(min_length=1, max_length=500)


class DeleteAsset(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field(min_length=1, max_length=2000)


def create_asset_router(get_store, get_authenticator, principal_dependency, get_runs=None, get_lifecycle=None):
    router = APIRouter(prefix="/contract-assets", tags=["Contract assets"], lifespan=reminder_lifespan(get_store, get_authenticator))

    def check_members(subjects, actor):
        auth = get_authenticator()
        valid = {actor.subject} if auth.environment != "production" else {
            str(identity["subject"]) for identity in auth.keys.values()
            if str(identity.get("tenantId")) == actor.tenant_id
        }
        if any(subject not in valid for subject in subjects):
            raise HTTPException(422, "共享成员必须属于当前租户")

    @router.post("/import")
    async def import_files(files: list[UploadFile] = File(...), actor: Principal = Depends(principal_dependency)):
        if actor.role not in {"admin", "legal_reviewer", "requester"}:
            raise HTTPException(403, "当前身份无导入权限")
        if not 1 <= len(files) <= 10:
            raise HTTPException(422, "每批请上传 1 至 10 个文件")
        results = []
        for upload in files:
            filename = (upload.filename or "document").replace("\\", "/").split("/")[-1]
            try:
                data = await upload.read(10 * 1024 * 1024 + 1)
                extracted = await run_in_threadpool(extract_document, filename, data)
                asset = await run_in_threadpool(get_store().create, filename, data, extracted, actor=actor)
                results.append({"filename": filename, "asset": asset})
            except (ValueError, RuntimeError) as exc:
                results.append({"filename": filename, "error": str(exc), "code": "unavailable" if isinstance(exc, RuntimeError) else "invalid_document"})
            finally:
                await upload.close()
        return {"results": results}

    @router.get("")
    def listing(q: str = Query(default="", max_length=500), tag: str = Query(default="", max_length=80), actor: Principal = Depends(principal_dependency)):
        return {"assets": call(lambda: get_store().list_assets(actor=actor, query=q, tag=tag))}

    @router.get("/search")
    def search(q: str = Query(min_length=1, max_length=500), mode: Literal["keyword", "semantic"] = "keyword", actor: Principal = Depends(principal_dependency)):
        if mode == "keyword":
            return {"citations": call(lambda: get_store().search(q, actor=actor)), "mode": mode}
        from .asset_semantic import semantic_search
        candidates = []
        for summary in get_store().list_assets(actor=actor):
            asset = get_store().get(summary["id"], actor=actor)
            for segment in asset["segments"]:
                candidates.append({"assetId": asset["id"], "title": asset["title"], "segmentId": segment["id"], "location": segment["location"], "text": segment["text"]})
                if len(candidates) > 500:
                    raise HTTPException(422, "语义检索当前最多支持 500 个文本段落，请缩小资产范围")
        return {"citations": call(lambda: semantic_search(q, candidates)), "mode": mode}

    @router.post("/ask")
    def ask(body: Question, actor: Principal = Depends(principal_dependency)):
        return call(lambda: get_store().ask(body.question, actor=actor))

    @router.post("/reminders/check")
    def check_reminders(actor: Principal = Depends(principal_dependency)):
        return {"result": call(lambda: get_store().dispatch_reminders(actor=actor))}

    @router.get("/notifications")
    def notifications(actor: Principal = Depends(principal_dependency)):
        # Also reconcile due items on inbox reads, so ordinary usage generates reminders.
        call(lambda: get_store().dispatch_reminders(actor=actor))
        return {"notifications": call(lambda: get_store().notifications(actor=actor))}

    @router.post("/notifications/{notification_id}/read")
    def read_notification(notification_id: str, actor: Principal = Depends(principal_dependency)):
        return call(lambda: get_store().mark_read(notification_id, actor=actor))

    @router.get("/{asset_id}")
    def detail(asset_id: str, actor: Principal = Depends(principal_dependency)):
        return call(lambda: get_store().get(asset_id, actor=actor))

    @router.patch("/{asset_id}")
    def update(asset_id: str, body: AssetUpdate, actor: Principal = Depends(principal_dependency)):
        call(lambda: get_store().get(asset_id, actor=actor))
        check_members(body.sharedWith, actor)
        return call(lambda: get_store().update(asset_id, actor=actor, expected_revision=body.expectedRevision, title=body.title, tags=body.tags, visibility=body.visibility, shared_with=body.sharedWith))

    @router.post("/{asset_id}/delete")
    def delete(asset_id: str, body: DeleteAsset, actor: Principal = Depends(principal_dependency)):
        if get_lifecycle and get_lifecycle().is_held(actor.tenant_id, "asset", asset_id):
            raise HTTPException(409, "资产处于法律保全状态，解除保全后才能删除")
        return call(lambda: get_store().soft_delete(asset_id, actor=actor, reason=body.reason))

    @router.post("/{asset_id}/restore")
    def restore(asset_id: str, actor: Principal = Depends(principal_dependency)):
        return call(lambda: get_store().restore(asset_id, actor=actor))

    @router.get("/{asset_id}/original")
    def original(asset_id: str, actor: Principal = Depends(principal_dependency)):
        filename, data = call(lambda: get_store().original(asset_id, actor=actor))
        return Response(data, media_type="application/octet-stream", headers={"Content-Disposition": f"attachment; filename=contract; filename*=UTF-8''{quote(filename, safe='')}"})

    @router.get('/{asset_id}/versions')
    def versions(asset_id: str, actor: Principal = Depends(principal_dependency)):
        return {'versions': call(lambda: get_store().versions(asset_id, actor=actor))}

    @router.get('/{asset_id}/versions/{version}')
    def version_detail(asset_id: str, version: int, actor: Principal = Depends(principal_dependency)):
        return call(lambda: get_store().get_version(asset_id, version, actor=actor))

    @router.get('/{asset_id}/versions/{version}/original')
    def version_original(asset_id: str, version: int, actor: Principal = Depends(principal_dependency)):
        filename, data = call(lambda: get_store().original(asset_id, actor=actor, version=version))
        return Response(data, media_type='application/octet-stream', headers={'Content-Disposition': f"attachment; filename=contract; filename*=UTF-8''{quote(filename, safe='')}"})

    @router.post('/{asset_id}/versions')
    async def add_version(asset_id: str, expectedRevision: int = Form(gt=0), file: UploadFile = File(...), actor: Principal = Depends(principal_dependency)):
        try:
            asset = call(lambda: get_store().get(asset_id, actor=actor))
            if actor.role not in {'admin', 'legal_reviewer', 'requester'} or (actor.role != 'admin' and asset['createdBy'] != actor.subject):
                raise HTTPException(403, '无权上传合同修订稿')
            if asset['revision'] != expectedRevision:
                raise HTTPException(409, 'revision conflict')
            filename = (file.filename or 'document').replace('\\', '/').split('/')[-1]
            data = await file.read(10 * 1024 * 1024 + 1)
            extracted = await run_in_threadpool(lambda: call(lambda: extract_document(filename, data)))
            return await run_in_threadpool(lambda: call(lambda: get_store().add_version(asset_id, filename, data, extracted, actor=actor, expected_revision=expectedRevision)))
        finally:
            await file.close()

    @router.get('/{asset_id}/diff')
    def diff(asset_id: str, from_version: int = Query(alias='from', gt=0), to_version: int = Query(alias='to', gt=0),
             runId: str | None = Query(default=None, min_length=1, max_length=100), actor: Principal = Depends(principal_dependency)):
        baseline = call(lambda: get_store().get_version(asset_id, from_version, actor=actor))
        report = None
        if runId is not None:
            run = get_runs().get_run(runId) if get_runs else None
            if run is None or run.get('tenantId') != actor.tenant_id:
                raise HTTPException(404, '审查运行不存在')
            if run.get('status') != 'done' or not isinstance(run.get('report'), dict):
                raise HTTPException(409, '风险影响需要已完成的审查报告')
            if run.get('inputTextHash') != hashlib.sha256(baseline['text'].encode('utf-8')).hexdigest():
                raise HTTPException(409, '报告未绑定此基线版本原文，请先审查所选基线版本')
            report = run['report']
        result = call(lambda: get_store().diff(asset_id, from_version, to_version, actor=actor, report=report))
        result['runId'] = runId
        return result

    @router.get("/{asset_id}/obligations")
    def obligations(asset_id: str, actor: Principal = Depends(principal_dependency)):
        return {"obligations": call(lambda: get_store().obligations(asset_id, actor=actor))}

    @router.post("/{asset_id}/obligations")
    def add_obligation(asset_id: str, body: ObligationCreate, actor: Principal = Depends(principal_dependency)):
        return call(lambda: get_store().add_obligation(asset_id, actor=actor, expected_revision=body.expectedRevision, title=body.title, due_at=body.dueAt, kind=body.kind))

    @router.post("/{asset_id}/obligations/{obligation_id}/complete")
    def complete_obligation(asset_id: str, obligation_id: str, body: Revision, actor: Principal = Depends(principal_dependency)):
        return call(lambda: get_store().complete_obligation(asset_id, obligation_id, actor=actor, expected_revision=body.expectedRevision))

    return router
