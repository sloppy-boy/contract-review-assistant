"""FastAPI 后端（SPEC 2.8）：/upload /report /health /export/word /balance，服务同一流水线。

- **并发隔离（必须）**：每个请求新建 graph/state 实例（黑板是内存态，共享实例会串数据）。
- 任务异步执行（进程内线程池 + 任务表，SPEC 不做数据库）。
- 离线演示缓存：真实 pipeline 跑出后导出（scripts/export_demo.py），严禁手工编报告。
- 线程安全：TASKS 任务表全部读写经 _tasks_lock（S2，防并发迭代崩溃）。
- 余额预警：/balance 探活账户余额（前端横幅 + 余额耗尽时阻止使用并提示停止服务）。
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
import copy
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable, Literal
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, ConfigDict

from .config import (
    BALANCE_WARN_THRESHOLD,
    DEEPSEEK_MODEL,
    DEEPSEEK_REVIEWER_MODEL,
    MAX_UPLOAD_CHARS,
    REVIEW_RUNS_PATH,
    using_mock,
)
from .export_word import report_to_docx
from .report_evidence import normalize_report
from .graph import run_pipeline
from .settings_store import active_llm_config, load_settings, public_settings, save_settings
from .review_runs import ReviewRunStore
from .playbook_api import create_playbook_router
from .playbook_defaults import built_in_playbook_snapshot
from .playbook_store import PlaybookStore
from .playbooks import ReviewScope
from .collaboration import CollaborationStore
from .collaboration_api import create_collaboration_router
from .asset_store import AssetStore
from .asset_api import create_asset_router
from .feedback import FeedbackStore
from .observability import TraceStore
from .security import ApiKeyAuthenticator, Principal
from .task_queue import TaskQueue
from .data_lifecycle import DataLifecycleStore
from .workflow_automation import IntegrationConfigStore
from .review_worker import execute_review_task

logger = logging.getLogger(__name__)
_REAL_THREAD = threading.Thread

app = FastAPI(title="合同审查助手", version="0.1.0")
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

review_run_store = ReviewRunStore(REVIEW_RUNS_PATH)
playbook_store = PlaybookStore(REVIEW_RUNS_PATH.with_name("playbooks.db"))
collaboration_store = CollaborationStore(REVIEW_RUNS_PATH.with_name("collaboration.db"))
asset_store = AssetStore(REVIEW_RUNS_PATH.with_name("assets.db"))
authenticator = ApiKeyAuthenticator.from_environment()
trace_store = TraceStore(REVIEW_RUNS_PATH.with_name("traces.db"))
feedback_store = FeedbackStore(REVIEW_RUNS_PATH.with_name("feedback.db"))
lifecycle_store = DataLifecycleStore(REVIEW_RUNS_PATH.with_name("lifecycle.db"))
integration_store = IntegrationConfigStore(REVIEW_RUNS_PATH.with_name("integrations.db"))


def _task_queue() -> TaskQueue:
    """Resolve beside the configured run store so tests and tenants stay isolated."""
    return TaskQueue(Path(review_run_store.path).with_name("tasks.db"))


TASK_LEASE_HEARTBEAT_SECONDS = max(1, int(os.environ.get("TASK_LEASE_HEARTBEAT_SECONDS", "30")))


def _schedule_lease_heartbeat(queue: TaskQueue, task_id: str, worker_id: str, tenant_id: str) -> None:
    """Renew a running task lease until the worker acknowledges or exits."""
    def renew() -> None:
        while True:
            time.sleep(TASK_LEASE_HEARTBEAT_SECONDS)
            try:
                alive = queue.heartbeat(task_id, worker_id, tenant_id=tenant_id)
            except Exception:
                logger.exception("任务 %s lease heartbeat failed", task_id)
                return
            if alive is None:
                return

    _REAL_THREAD(target=renew, daemon=True).start()

# 后端并发限制：同时最多运行 N 条流水线（防多用户并发打爆 LLM API 限流）
PIPELINE_MAX_CONCURRENT = int(os.environ.get("PIPELINE_MAX_CONCURRENT", "2"))
_pipeline_semaphore = threading.Semaphore(PIPELINE_MAX_CONCURRENT)

# 文件名消毒（S1）：HTTP 头禁止控制字符/引号/反斜杠（防头注入）；换行/CR 亦属控制字符
_FILENAME_UNSAFE_RE = re.compile(r'[\x00-\x1f\x7f"\\/:*?<>|]')


@app.middleware("http")
async def production_api_prefix(request: Request, call_next):
    """开发期由 Vite 代理去掉 /api；生产单容器复用同一公开路径。"""
    if request.scope["path"].startswith("/api/"):
        request.scope["path"] = request.scope["path"][4:]
    return await call_next(request)


def _sanitize_filename(name: str, fallback: str = "contract-review-report") -> str:
    """消毒用户提交的合同名 → 安全的文件名主干（不含扩展名）。"""
    cleaned = _FILENAME_UNSAFE_RE.sub("_", str(name or "")).strip(" .")
    return cleaned[:60] or fallback


class ExportWordReq(BaseModel):
    report: dict | None = None
    runId: str | None = None


class FindingDispositionReq(BaseModel):
    decision: str
    reason: str = ""


class FeedbackReq(BaseModel):
    decision: str
    reason: str = ""


class CancelRunReq(BaseModel):
    reason: str


class LegalHoldReq(BaseModel):
    resourceType: Literal["asset", "review_run", "collaboration_request", "playbook_version"]
    resourceId: str
    reason: str


class RetentionPurgeReq(BaseModel):
    resourceType: Literal["asset", "review_run", "collaboration_request", "playbook_version"] = "asset"
    before: str
    dryRun: bool = True


@dataclass(frozen=True)
class _LifecycleAdapter:
    exists: Callable[[str], bool]
    candidates: Callable[[str], list[dict]]
    delete: Callable[[dict], object]


def _lifecycle_adapter(resource_type: str, tenant_id: str) -> _LifecycleAdapter:
    if resource_type == "asset":
        return _LifecycleAdapter(
            exists=lambda resource_id: asset_store.exists_for_tenant(resource_id, tenant_id=tenant_id),
            candidates=lambda before: asset_store.deleted_candidates(tenant_id=tenant_id, before=before),
            delete=lambda record: asset_store.purge_deleted(record["resourceId"], tenant_id=tenant_id),
        )
    if resource_type == "review_run":
        return _LifecycleAdapter(
            exists=lambda resource_id: bool((review_run_store.get_run(resource_id) or {}).get("tenantId") == tenant_id),
            candidates=lambda before: review_run_store.retention_candidates(tenant_id=tenant_id, before=before),
            delete=lambda record: _purge_review_run(record["resourceId"], tenant_id),
        )
    if resource_type == "collaboration_request":
        return _LifecycleAdapter(
            exists=lambda resource_id: collaboration_store.get(resource_id, actor=Principal("governance", tenant_id, "admin")) is not None,
            candidates=lambda before: collaboration_store.retention_candidates(tenant_id=tenant_id, before=before),
            delete=lambda record: collaboration_store.purge_request(record["resourceId"], tenant_id=tenant_id),
        )
    if resource_type == "playbook_version":
        def exists(resource_id: str) -> bool:
            book_id, separator, raw_version = resource_id.rpartition(":")
            try:
                version = int(raw_version) if separator else 0
            except ValueError:
                version = 0
            return bool(book_id and version > 0 and playbook_store.get_version(book_id, version, tenant_id=tenant_id) is not None)
        return _LifecycleAdapter(
            exists=exists,
            candidates=lambda before: playbook_store.retention_candidates(tenant_id=tenant_id, before=before),
            delete=lambda record: playbook_store.purge_version(*record["resourceId"].rsplit(":", 1), tenant_id=tenant_id),
        )
    raise ValueError("unknown lifecycle resource type")


def _purge_review_run(run_id: str, tenant_id: str) -> bool:
    deleted = review_run_store.purge_run(run_id, tenant_id=tenant_id)
    feedback_store.delete_run(tenant_id=tenant_id, run_id=run_id)
    trace_store.delete_run(tenant_id=tenant_id, run_id=run_id)
    return deleted


class IntegrationConfigReq(BaseModel):
    model_config = ConfigDict(extra="forbid")
    integrationKind: Literal["generic", "enterprise_im", "ticket", "procurement_crm", "electronic_signature"]
    url: str
    allowedHosts: list[str]
    secret: str | None = None
    timeoutSeconds: float = 5.0
    enabled: bool = False


def current_principal(x_api_key: str | None = Header(default=None)) -> Principal:
    try:
        return authenticator.authenticate(x_api_key)
    except PermissionError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _require_governance(principal: Principal, *roles: str) -> None:
    try:
        ApiKeyAuthenticator.require_role(principal, *roles)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _dispatch_integration_event(event: dict) -> dict:
    """Dispatch a data-minimized event; integration failures never break workflow writes."""
    try:
        key = f"{event.get('type')}:{event.get('tenantId')}:{event.get('resourceId')}:{event.get('createdAt')}"
        return integration_store.dispatch(event, idempotency_key=key)
    except Exception:
        logger.exception("外部集成事件派发失败")
        return {"delivered": False, "configured": False}


app.include_router(create_playbook_router(lambda: playbook_store, current_principal))
app.include_router(create_collaboration_router(
    lambda: collaboration_store, lambda: review_run_store, lambda: authenticator, current_principal,
    dispatch_event=lambda event: _dispatch_integration_event(event),
))
app.include_router(create_asset_router(lambda: asset_store, lambda: authenticator, current_principal, lambda: review_run_store, lambda: lifecycle_store))


@app.get("/platform/metrics")
def platform_metrics(principal: Principal = Depends(current_principal)) -> dict:
    _require_governance(principal, "admin", "legal_reviewer")
    return trace_store.summary(principal.tenant_id)


@app.get("/platform/integrations")
def list_integrations(principal: Principal = Depends(current_principal)) -> dict:
    _require_governance(principal, "admin")
    return {"integrations": integration_store.list_configs(tenant_id=principal.tenant_id)}


@app.get("/platform/integrations/deliveries")
def list_integration_deliveries(principal: Principal = Depends(current_principal)) -> dict:
    _require_governance(principal, "admin")
    return {"deliveries": integration_store.attempts(tenant_id=principal.tenant_id)}


@app.put("/platform/integrations/{integration_kind}")
def configure_integration(integration_kind: Literal["generic", "enterprise_im", "ticket", "procurement_crm", "electronic_signature"], req: IntegrationConfigReq, principal: Principal = Depends(current_principal)) -> dict:
    _require_governance(principal, "admin")
    if integration_kind != req.integrationKind:
        raise HTTPException(status_code=422, detail="integration kind mismatch")
    try:
        return integration_store.configure(tenant_id=principal.tenant_id, integration_kind=req.integrationKind, url=req.url, allowed_hosts=req.allowedHosts, secret=req.secret, enabled=req.enabled, timeout_seconds=req.timeoutSeconds)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/platform/integrations/{integration_kind}")
def delete_integration(integration_kind: Literal["generic", "enterprise_im", "ticket", "procurement_crm", "electronic_signature"], principal: Principal = Depends(current_principal)) -> dict:
    _require_governance(principal, "admin")
    if not integration_store.delete(tenant_id=principal.tenant_id, integration_kind=integration_kind):
        raise HTTPException(status_code=404, detail="integration not found")
    return {"deleted": True}


@app.get("/platform/feedback/export")
def export_feedback(principal: Principal = Depends(current_principal)) -> dict:
    _require_governance(principal, "admin", "legal_reviewer")
    return {"examples": feedback_store.export_training_examples(principal.tenant_id)}


@app.get("/platform/legal-holds")
def list_legal_holds(principal: Principal = Depends(current_principal)) -> dict:
    _require_governance(principal, "admin", "legal_reviewer")
    return {"holds": lifecycle_store.holds(principal.tenant_id)}


@app.post("/platform/legal-holds")
def place_legal_hold(req: LegalHoldReq, principal: Principal = Depends(current_principal)) -> dict:
    _require_governance(principal, "admin", "legal_reviewer")
    if not req.resourceId.strip():
        raise HTTPException(422, "resourceId must not be blank")
    if not _lifecycle_adapter(req.resourceType, principal.tenant_id).exists(req.resourceId):
        raise HTTPException(404, "resource not found")
    try:
        return lifecycle_store.place_hold(tenant_id=principal.tenant_id, resource_type=req.resourceType, resource_id=req.resourceId, reason=req.reason, actor=principal.subject)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.delete("/platform/legal-holds/{resource_type}/{resource_id}")
def release_legal_hold(resource_type: Literal["asset", "review_run", "collaboration_request", "playbook_version"], resource_id: str, principal: Principal = Depends(current_principal)) -> dict:
    _require_governance(principal, "admin", "legal_reviewer")
    result = lifecycle_store.release_hold(principal.tenant_id, resource_type, resource_id, actor=principal.subject)
    if result is None:
        raise HTTPException(404, "legal hold not found")
    return result


@app.get("/platform/data-lifecycle/events")
def lifecycle_events(principal: Principal = Depends(current_principal)) -> dict:
    _require_governance(principal, "admin", "legal_reviewer")
    return {"events": lifecycle_store.events(principal.tenant_id)}


@app.post("/platform/data-lifecycle/purge")
def purge_retention(req: RetentionPurgeReq, principal: Principal = Depends(current_principal)) -> dict:
    _require_governance(principal, "admin")
    try:
        adapter = _lifecycle_adapter(req.resourceType, principal.tenant_id)
        records = adapter.candidates(req.before)
        delete = adapter.delete
        return lifecycle_store.purge(records, tenant_id=principal.tenant_id, before=req.before, actor=principal.subject, dry_run=req.dryRun, delete=delete)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/platform/tasks")
def list_platform_tasks(
    status: str | None = None,
    kind: str | None = None,
    limit: int = 100,
    principal: Principal = Depends(current_principal),
) -> dict:
    """Expose queue control-plane metadata to governance operators."""
    _require_governance(principal, "admin", "legal_reviewer")
    try:
        return {"tasks": _task_queue().list_tasks(status=status, kind=kind, tenant_id=principal.tenant_id, limit=limit)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/platform/tasks/requeue-stale")
def requeue_stale_platform_tasks(lease_seconds: int = 300, principal: Principal = Depends(current_principal)) -> dict:
    _require_governance(principal, "admin")
    try:
        return {"requeued": _task_queue().requeue_stale(datetime.now(UTC), lease_seconds=lease_seconds, tenant_id=principal.tenant_id)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/platform/tasks/{task_id}")
def get_platform_task(task_id: str, principal: Principal = Depends(current_principal)) -> dict:
    _require_governance(principal, "admin", "legal_reviewer")
    task = _task_queue().get(task_id, tenant_id=principal.tenant_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@app.post("/platform/tasks/{task_id}/retry")
def retry_platform_task(task_id: str, principal: Principal = Depends(current_principal)) -> dict:
    _require_governance(principal, "admin")
    try:
        task = _task_queue().retry_dead(task_id, tenant_id=principal.tenant_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@app.post("/platform/tasks/{task_id}/cancel")
def cancel_platform_task(task_id: str, principal: Principal = Depends(current_principal)) -> dict:
    _require_governance(principal, "admin", "legal_reviewer")
    task = _task_queue().cancel(task_id, tenant_id=principal.tenant_id)
    if task is None:
        raise HTTPException(status_code=404, detail="task not found")
    return task


@app.post("/export/word")
def export_word(req: ExportWordReq, principal: Principal = Depends(current_principal)) -> Response:
    """Bound exports use only persisted report and audit records; legacy JSON is unbound."""
    audit = None
    if req.runId:
        run = review_run_store.get_run(req.runId)
        if run is None or run["tenantId"] != principal.tenant_id:
            raise HTTPException(status_code=404, detail="task not found")
        if run["status"] != "done" or not run.get("report"):
            raise HTTPException(status_code=409, detail="review run has no completed report")
        export_report = normalize_report(run["report"], dispositions=review_run_store.get_dispositions(req.runId))
        audit = {"runId": req.runId, "createdAt": run["createdAt"], "updatedAt": run["updatedAt"],
                 "events": review_run_store.list_events(req.runId)}
    elif req.report is not None:
        export_report = normalize_report(req.report, dispositions={})
    else:
        raise HTTPException(status_code=422, detail="runId or report is required")
    try:
        docx_bytes = report_to_docx(export_report, audit=audit)
    except Exception:  # S17：不回显内部异常细节（信息泄露）；具体原因记服务端日志
        logger.exception("Word 导出失败")
        raise HTTPException(status_code=500, detail="Word 导出失败：报告数据不合法")
    name = _sanitize_filename(export_report.get("contract", {}).get("name"))
    # S1：filename 仅 ASCII 安全字符；中文经 RFC 5987 filename* 传递（latin-1 500 根除）
    ascii_name = name.encode("ascii", "ignore").decode("ascii").strip(" .") or "contract-review-report"
    filename = f"{name}.docx"
    disposition = (
        f'attachment; filename="{ascii_name}.docx"; filename*=UTF-8\'\'{quote(filename, safe="")}'
    )
    return Response(
        content=docx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": disposition},
    )


@app.get("/health")
def health() -> dict:
    """健康检查：mock 状态 + 当前生效的模型路由（主链路/复核，均从运行时设置解析）。"""
    main_cfg = active_llm_config("main")
    review_cfg = active_llm_config("review")
    return {
        "status": "ok",
        "mock": using_mock(),
        "model": main_cfg["model"] if main_cfg else DEEPSEEK_MODEL,
        "modelProvider": main_cfg["provider"] if main_cfg else "deepseek",
        "reviewerModel": review_cfg["model"] if review_cfg else DEEPSEEK_REVIEWER_MODEL,
        "reviewerProvider": review_cfg["provider"] if review_cfg else "deepseek",
    }


@app.get("/balance")
def balance() -> dict:
    """账户余额探活（前端预警横幅 / 余额耗尽阻止使用）。

    - 仅对 DeepSeek 官方端点查询余额（/user/balance 为标准接口）；
      其他供应商（opencode-go 等订阅制）无余额接口 → available=None（前端不提示）。
    - mock 模式（无 key / DSH_FORCE_MOCK）→ available=None（前端不提示）。
    - available=False / balance<=0：前端提示"API 供应商停止服务"并阻止提交。
    """
    cfg = active_llm_config("main")
    is_deepseek = bool(cfg and "api.deepseek.com" in (cfg["baseUrl"] or ""))
    if using_mock() or not cfg or not cfg["apiKey"] or not is_deepseek:
        note = "" if (using_mock() or not cfg or not cfg["apiKey"]) else "当前供应商为订阅制，无余额查询接口"
        return {
            "available": None,
            "balance": None,
            "threshold": BALANCE_WARN_THRESHOLD,
            "mock": using_mock() or not cfg or not cfg["apiKey"],
            "note": note,
        }
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.get(
                f"{cfg['baseUrl']}/user/balance",
                headers={"Authorization": f"Bearer {cfg['apiKey']}", "Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
        infos = data.get("balance_infos") or []
        # 只统计人民币账户（DeepSeek 主账户为 CNY）；多账户求和
        total = sum(
            float(i.get("total_balance") or 0)
            for i in infos
            if str(i.get("currency", "")).upper() == "CNY"
        )
        return {
            "available": bool(data.get("is_available")),  # 账户可用性（余额耗尽时为 False）
            "balance": round(total, 2),
            "threshold": BALANCE_WARN_THRESHOLD,
            "mock": False,
        }
    except Exception as e:
        # 余额查询失败：不阻塞使用，前端显示"无法查询余额"
        logger.warning("余额查询失败：%s", e)
        return {
            "available": None,
            "balance": None,
            "threshold": BALANCE_WARN_THRESHOLD,
            "mock": False,
            "error": "余额查询失败（可能为网络/供应商接口波动）",
        }


# ================================================================ 设置 / 供应商管理
@app.get("/settings")
def get_settings() -> dict:
    """设置页数据（脱敏）：providers（baseUrl/hasKey/models/价格）+ 模型路由选择。"""
    return public_settings()


@app.get("/providers/{pid}/models")
def provider_models(pid: str) -> dict:
    """实时拉取指定供应商的模型列表（OpenAI 兼容 /models；失败回退本地预置目录）。"""
    cfg = load_settings()
    prov = (cfg.get("providers") or {}).get(pid)
    if not prov or not prov.get("apiKey") or not prov.get("baseUrl"):
        raise HTTPException(status_code=400, detail=f"供应商 {pid} 未配置（缺 baseUrl/apiKey）")
    try:
        with httpx.Client(timeout=15) as client:
            resp = client.get(
                f"{prov['baseUrl'].rstrip('/')}/models",
                headers={"Authorization": f"Bearer {prov['apiKey']}", "Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
        ids = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
        if ids:
            return {"provider": pid, "models": ids, "source": "live"}
        # 空列表：回退本地预置
        return {"provider": pid, "models": prov.get("models", []), "source": "fallback"}
    except Exception as e:
        logger.warning("拉取 %s 模型列表失败：%s", pid, e)
        return {"provider": pid, "models": prov.get("models", []), "source": "fallback", "error": str(e)[:120]}


class ProviderTestReq(BaseModel):
    """测试连接请求：用设置中的 key 发起最小 chat 调用验证连通性。"""

    model: str


@app.post("/providers/{pid}/test")
def provider_test(pid: str, req: ProviderTestReq) -> dict:
    """测试供应商连通性：最小 chat 调用（max_tokens=8），验证 key + 模型可用。"""
    cfg = load_settings()
    prov = (cfg.get("providers") or {}).get(pid)
    if not prov or not prov.get("apiKey") or not prov.get("baseUrl"):
        raise HTTPException(status_code=400, detail=f"供应商 {pid} 未配置（缺 baseUrl/apiKey）")
    if not req.model:
        raise HTTPException(status_code=400, detail="请先选择要测试的模型")
    try:
        with httpx.Client(timeout=40) as client:
            resp = client.post(
                f"{prov['baseUrl'].rstrip('/')}/chat/completions",
                headers={
                    "Authorization": f"Bearer {prov['apiKey']}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": req.model,
                    "messages": [{"role": "user", "content": "回复OK"}],
                    "max_tokens": 16,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        usage = data.get("usage") or {}
        return {"ok": True, "provider": pid, "model": req.model, "usage": {
            "in": usage.get("prompt_tokens", 0), "out": usage.get("completion_tokens", 0),
        }}
    except Exception as e:
        return {"ok": False, "provider": pid, "model": req.model, "error": str(e)[:200]}


class SettingsUpdateReq(BaseModel):
    """设置保存请求：providers / mainModel / reviewModel / common（apiKey 空 = 保留原值）。"""

    providers: dict = {}
    mainModel: dict | None = None
    reviewModel: dict | None = None
    common: dict | None = None


@app.put("/settings")
def put_settings(req: SettingsUpdateReq) -> dict:
    """保存设置（写 settings.json）。模型路由下次审查即时生效；common 类重启后端生效。"""
    saved = save_settings(
        {
            "providers": req.providers,
            "mainModel": req.mainModel,
            "reviewModel": req.reviewModel,
            "common": req.common,
        }
    )
    return public_settings()


@app.post("/upload")
async def upload(
    file: UploadFile | None = None,
    text: str | None = Form(default=None),
    contract_type: str = Form(default="purchase"),
    playbook_id: str | None = Form(default=None),
    playbook_version: int | None = Form(default=None, gt=0),
    jurisdiction: str = Form(default="CN"),
    business_scenario: str = Form(default="general"),
    effective_scope: str = Form(default="*"),
    principal: Principal = Depends(current_principal),
) -> dict:
    """上传合同（文件或文本）→ 返回 taskId（异步跑流水线）。"""
    if active_llm_config("main") is None:
        raise HTTPException(
            status_code=503,
            detail="审查模型尚未配置。请先在设置页配置并测试可用的主审查模型；离线演示可继续使用。",
        )
    if file is not None:
        content = (await file.read()).decode("utf-8", errors="ignore")
    elif text:
        content = text
    else:
        raise HTTPException(status_code=400, detail="需提供文件或文本")
    if not content.strip():
        raise HTTPException(status_code=400, detail="合同内容为空")
    if len(content) > MAX_UPLOAD_CHARS:
        raise HTTPException(
            status_code=413,
            detail=f"合同内容过长（{len(content)} 字符，上限 {MAX_UPLOAD_CHARS}），请拆分后上传",
        )
    contract_type = contract_type.strip()
    jurisdiction = jurisdiction.strip()
    business_scenario = business_scenario.strip()
    effective_scope = effective_scope.strip()
    if not all((contract_type, jurisdiction, business_scenario, effective_scope)):
        raise HTTPException(status_code=422, detail="review scope fields must not be blank")
    if playbook_id is not None:
        playbook_id = playbook_id.strip()
        if not playbook_id:
            raise HTTPException(status_code=422, detail="playbook_id must not be blank")
    if (playbook_id is None) != (playbook_version is None):
        raise HTTPException(status_code=422, detail="playbook_id and playbook_version must be supplied together")
    if playbook_id is not None:
        try:
            snapshot = playbook_store.resolve_snapshot(
                playbook_id, playbook_version, tenant_id=principal.tenant_id,
                scope=ReviewScope(contractType=contract_type, jurisdiction=jurisdiction,
                                  businessScenario=business_scenario, effectiveScope=effective_scope),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="playbook version not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    else:
        if jurisdiction != "CN" or business_scenario != "general":
            raise HTTPException(status_code=422, detail="select a published playbook for this jurisdiction or scenario")
        try:
            snapshot = built_in_playbook_snapshot(contract_type, tenant_id=principal.tenant_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid contract type") from exc
    task = review_run_store.create_run(
        contract_type=contract_type, tenant_id=principal.tenant_id, playbook_snapshot=snapshot, input_text=content,
    )
    task_id = task["id"]
    _task_queue().enqueue(task_id, kind="review", tenant_id=principal.tenant_id, payload={"runId": task_id})
    threading.Thread(target=_execute_review_task, args=(task_id, content, contract_type, principal.tenant_id), daemon=True).start()
    return {"taskId": task_id, "status": "running"}


@app.get("/report/{task_id}")
def report(task_id: str, principal: Principal = Depends(current_principal)) -> dict:
    """轮询任务结果（报告 JSON）。"""
    task = review_run_store.get_run(task_id)
    if task is None or task["tenantId"] != principal.tenant_id:
        raise HTTPException(status_code=404, detail="task not found")

    # 人工裁决属于审查运行的附加事实，读取时叠加到原始报告，避免改写
    # LLM 产出的可追溯证据和报告快照。
    if task.get("report"):
        task = copy.deepcopy(task)
        task["report"].setdefault("meta", {}).setdefault("stageTimes", task.get("stageTimes", [0, 0, 0, 0]))
        task["report"] = normalize_report(task["report"], dispositions=review_run_store.get_dispositions(task_id))
    return task


@app.get("/review-runs/{task_id}/events")
def review_run_events(task_id: str, after_id: int = 0, principal: Principal = Depends(current_principal)) -> dict:
    task = review_run_store.get_run(task_id)
    if task is None or task["tenantId"] != principal.tenant_id:
        raise HTTPException(status_code=404, detail="task not found")
    return {"events": review_run_store.list_events(task_id, after_id=after_id)}


@app.get("/review-runs")
def review_history(
    status: str | None = None,
    limit: int = 100,
    principal: Principal = Depends(current_principal),
) -> dict:
    """审查历史列表；严格按当前租户隔离。"""
    return {"runs": review_run_store.list_runs(tenant_id=principal.tenant_id, status=status, limit=limit)}


@app.post("/review-runs/{task_id}/cancel")
def cancel_review_run(task_id: str, req: CancelRunReq, principal: Principal = Depends(current_principal)) -> dict:
    task = review_run_store.get_run(task_id)
    if task is None or task["tenantId"] != principal.tenant_id:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        ApiKeyAuthenticator.require_role(principal, "admin", "legal_reviewer", "requester")
        result = review_run_store.cancel_run(task_id, reason=req.reason, actor=principal.subject)
        _task_queue().cancel(task_id, tenant_id=principal.tenant_id)
        return result
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/review-runs/{task_id}/findings/{finding_id}/disposition")
def set_finding_disposition(task_id: str, finding_id: str, req: FindingDispositionReq, principal: Principal = Depends(current_principal)) -> dict:
    task = review_run_store.get_run(task_id)
    if task is None or task["tenantId"] != principal.tenant_id:
        raise HTTPException(status_code=404, detail="task not found")
    try:
        ApiKeyAuthenticator.require_role(principal, "admin", "legal_reviewer")
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not any(r.get("id") == finding_id for r in (task.get("report") or {}).get("risks", [])):
        raise HTTPException(status_code=404, detail="finding not found")
    try:
        return review_run_store.set_disposition(task_id, finding_id, req.decision, req.reason, actor=principal.subject)
    except KeyError:
        raise HTTPException(status_code=404, detail="task not found")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.post("/review-runs/{task_id}/findings/{finding_id}/feedback")
def record_feedback(task_id: str, finding_id: str, req: FeedbackReq, principal: Principal = Depends(current_principal)) -> dict:
    ApiKeyAuthenticator.require_role(principal, "admin", "legal_reviewer")
    feedback_store.record(tenant_id=principal.tenant_id, run_id=task_id, finding_id=finding_id, decision=req.decision, reason=req.reason)
    return {"ok": True}


def _execute_review_task(task_id: str, content: str, contract_type: str, tenant_id: str = "local") -> None:
    execute_review_task(
        task_id, content, contract_type, tenant_id,
        queue_factory=_task_queue, run_store=review_run_store, pipeline=run_pipeline,
        semaphore=_pipeline_semaphore, schedule_heartbeat=_schedule_lease_heartbeat, logger=logger,
    )

@app.get("/{spa_path:path}", include_in_schema=False)
def frontend(spa_path: str) -> Response:
    """生产环境单容器托管 Vue 构建产物，并支持前端路由刷新。"""
    index = FRONTEND_DIST / "index.html"
    if not index.exists():
        raise HTTPException(status_code=503, detail="前端构建产物不存在，请先执行 npm run build")
    candidate = FRONTEND_DIST / spa_path
    if spa_path and candidate.is_file() and candidate.resolve().is_relative_to(FRONTEND_DIST.resolve()):
        return FileResponse(candidate)
    return FileResponse(index)
