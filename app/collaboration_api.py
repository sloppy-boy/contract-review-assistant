"""Review collaboration HTTP boundary: identity, membership and run resolution."""
from __future__ import annotations

from collections.abc import Callable
from typing import Literal
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .collaboration import CollaborationStore, RequestContent
from .review_runs import ReviewRunStore
from .security import ApiKeyAuthenticator, Principal
from .workflow_automation import BusinessCalendar
from .collaboration_reminders import dispatch_collaboration_reminders
from .asset_reminders import periodic_lifespan
from .workflow_automation import build_integration_event
from .http_errors import call as _call


class RevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    expectedRevision: int = Field(gt=0, strict=True)


class AssignmentRequest(RevisionRequest):
    assignee: str = Field(min_length=1)
    approver: str = Field(min_length=1)
    dueAt: str = Field(min_length=1)


class RunRequest(RevisionRequest):
    runId: str = Field(min_length=1)


class ReasonRequest(RevisionRequest):
    reason: str = Field(min_length=1, max_length=10000)


class DecisionRequest(ReasonRequest):
    decision: Literal["approved", "changes_requested"]


class CommentRequest(RevisionRequest):
    body: str = Field(min_length=1, max_length=10000)
    mentions: list[str] = Field(default_factory=list, max_length=100)


class SlaCalculationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    startedAt: datetime
    businessMinutes: int = Field(gt=0, le=525600, strict=True)
    timezone: str = "Asia/Shanghai"
    workdays: tuple[int, ...] = (0, 1, 2, 3, 4)
    workStart: str = "09:00"
    workEnd: str = "18:00"
    holidays: tuple[date, ...] = ()


def create_collaboration_router(
    get_store: Callable[[], CollaborationStore], get_runs: Callable[[], ReviewRunStore],
    get_authenticator: Callable[[], ApiKeyAuthenticator], principal_dependency: Callable[..., Principal],
    dispatch_event: Callable[[dict], object] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/collaboration", tags=["Collaboration"], lifespan=periodic_lifespan(lambda: dispatch_collaboration_reminders(get_store(), get_authenticator())))

    def members_for(actor):
        authenticator = get_authenticator()
        if authenticator.environment != "production":
            return [{"subject": actor.subject, "role": actor.role}]
        members = {}
        rank = {"reader": 0, "requester": 1, "legal_reviewer": 2, "admin": 3}
        for identity in authenticator.keys.values():
            if str(identity.get("tenantId")) != actor.tenant_id:
                continue
            subject, role = str(identity["subject"]), str(identity["role"])
            if subject not in members or rank.get(role, -1) > rank.get(members[subject], -1):
                members[subject] = role
        return [{"subject": subject, "role": role} for subject, role in sorted(members.items())]

    def required(request_id, actor):
        request = get_store().get(request_id, actor=actor)
        if request is None:
            raise HTTPException(status_code=404, detail="request not found")
        return request

    def resolve_run(run_id, actor):
        run = get_runs().get_run(run_id)
        if run is None or run["tenantId"] != actor.tenant_id:
            raise HTTPException(status_code=404, detail="review run not found")
        return run

    def change(operation, *, actor, event_type: str):
        result = _call(operation)
        if dispatch_event is not None:
            event = build_integration_event(
                event_type, tenant_id=actor.tenant_id, resource_id=result["id"],
                summary=f"审查协作状态更新：{result['status']}",
            )
            dispatch_event(event)
        return result

    @router.get("/me")
    def me(actor: Principal = Depends(principal_dependency)):
        return {"subject": actor.subject, "tenantId": actor.tenant_id, "role": actor.role}

    @router.get("/members")
    def members(actor: Principal = Depends(principal_dependency)):
        return {"members": members_for(actor)}

    @router.post("/sla/calculate")
    def calculate_sla(req: SlaCalculationRequest, actor: Principal = Depends(principal_dependency)):
        if actor.role not in {"admin", "legal_reviewer", "requester"}:
            raise HTTPException(status_code=403, detail="当前身份无权计算审查 SLA")
        if req.startedAt.tzinfo is None or req.startedAt.utcoffset() is None:
            raise HTTPException(status_code=422, detail="startedAt 必须包含时区")
        calendar = BusinessCalendar(timezone=req.timezone, workdays=req.workdays, work_start=req.workStart, work_end=req.workEnd, holidays=req.holidays)
        return {"dueAt": calendar.add_business_minutes(req.startedAt, req.businessMinutes).isoformat()}

    @router.post("/requests", status_code=201)
    def create(content: RequestContent, actor: Principal = Depends(principal_dependency)):
        return change(lambda: get_store().create(content, actor=actor), actor=actor, event_type="request.created")

    @router.get("/requests")
    def list_requests(status: str | None = None, actor: Principal = Depends(principal_dependency)):
        return {"requests": get_store().list_requests(actor=actor, status=status)}

    @router.get("/requests/{request_id}")
    def detail(request_id: str, actor: Principal = Depends(principal_dependency)):
        return required(request_id, actor)

    @router.get("/requests/{request_id}/events")
    def events(request_id: str, actor: Principal = Depends(principal_dependency)):
        return {"events": _call(lambda: get_store().events(request_id, actor=actor))}

    @router.post("/requests/{request_id}/submit")
    def submit(request_id: str, req: RevisionRequest, actor: Principal = Depends(principal_dependency)):
        recipients = [member["subject"] for member in members_for(actor) if member["role"] in {"admin", "legal_reviewer"}]
        return change(lambda: get_store().submit(request_id, actor=actor, expected_revision=req.expectedRevision, recipients=recipients), actor=actor, event_type="request.submitted")

    @router.post("/requests/{request_id}/assign")
    def assign(request_id: str, req: AssignmentRequest, actor: Principal = Depends(principal_dependency)):
        required(request_id, actor)
        reviewers = {member["subject"] for member in members_for(actor) if member["role"] in {"admin", "legal_reviewer"}}
        if req.assignee not in reviewers or req.approver not in reviewers:
            raise HTTPException(status_code=422, detail="处理人和审批人必须是本工作区的法务或管理员")
        return change(lambda: get_store().assign(request_id, actor=actor, expected_revision=req.expectedRevision, assignee=req.assignee, approver=req.approver, due_at=req.dueAt), actor=actor, event_type="request.assigned")

    @router.post("/requests/{request_id}/run")
    def bind_run(request_id: str, req: RunRequest, actor: Principal = Depends(principal_dependency)):
        required(request_id, actor)
        run = resolve_run(req.runId, actor)
        return change(lambda: get_store().bind_run(request_id, actor=actor, expected_revision=req.expectedRevision, run=run), actor=actor, event_type="request.run_bound")

    @router.post("/requests/{request_id}/request-approval")
    def request_approval(request_id: str, req: RevisionRequest, actor: Principal = Depends(principal_dependency)):
        request = required(request_id, actor)
        if not request["linkedRunId"]:
            raise HTTPException(status_code=409, detail="请先关联已完成的审查报告")
        run = resolve_run(request["linkedRunId"], actor)
        dispositions = get_runs().get_dispositions(run["id"])
        return change(lambda: get_store().request_approval(request_id, actor=actor, expected_revision=req.expectedRevision, run=run, dispositions=dispositions), actor=actor, event_type="request.approval_requested")

    @router.post("/requests/{request_id}/decide")
    def decide(request_id: str, req: DecisionRequest, actor: Principal = Depends(principal_dependency)):
        return change(lambda: get_store().decide(request_id, actor=actor, expected_revision=req.expectedRevision, decision=req.decision, reason=req.reason), actor=actor, event_type=f"request.{req.decision}")

    @router.post("/requests/{request_id}/cancel")
    def cancel(request_id: str, req: ReasonRequest, actor: Principal = Depends(principal_dependency)):
        return change(lambda: get_store().cancel(request_id, actor=actor, expected_revision=req.expectedRevision, reason=req.reason), actor=actor, event_type="request.cancelled")

    @router.post("/requests/{request_id}/comments")
    def comment(request_id: str, req: CommentRequest, actor: Principal = Depends(principal_dependency)):
        required(request_id, actor)
        members = {member["subject"] for member in members_for(actor)}
        if any(subject not in members for subject in req.mentions):
            raise HTTPException(status_code=422, detail="只能提及本工作区成员")
        return change(lambda: get_store().comment(request_id, actor=actor, expected_revision=req.expectedRevision, body=req.body, mentions=req.mentions), actor=actor, event_type="request.commented")

    @router.get("/notifications")
    def notifications(unread_only: bool = False, actor: Principal = Depends(principal_dependency)):
        dispatch_collaboration_reminders(get_store(), get_authenticator())
        return {"notifications": get_store().notifications(actor=actor, unread_only=unread_only)}

    @router.post("/notifications/{notification_id}/read")
    def mark_read(notification_id: int, actor: Principal = Depends(principal_dependency)):
        return _call(lambda: get_store().mark_read(notification_id, actor=actor))

    return router
