from datetime import UTC, date, datetime
import pytest

from app.workflow_automation import BusinessCalendar, IntegrationConfigStore, IntegrationDispatcher, OutboundPolicy, SignedWebhook, build_integration_event


def test_business_calendar_skips_weekends_holidays_and_nonworking_hours():
    calendar = BusinessCalendar(timezone="Asia/Shanghai", workdays=(0, 1, 2, 3, 4), work_start="09:00", work_end="18:00", holidays=(date(2026, 10, 1),))
    # Wednesday Sep 30 17:00 local + 2 business hours => Friday Oct 2 10:00 local.
    result = calendar.add_business_minutes(datetime(2026, 9, 30, 9, 0, tzinfo=UTC), 120)
    assert result == datetime(2026, 10, 2, 2, 0, tzinfo=UTC)


@pytest.mark.parametrize("url", [
    "http://example.com/hook", "https://localhost/hook", "https://127.0.0.1/hook",
    "https://10.1.2.3/hook", "https://169.254.1.1/hook", "https://user:pass@example.com/hook",
])
def test_outbound_policy_rejects_unsafe_targets(url):
    with pytest.raises(ValueError):
        OutboundPolicy(url=url, allowed_hosts=("example.com",), secret="test-secret")


def test_signed_webhook_uses_allowlist_signature_idempotency_and_no_redirect(monkeypatch):
    calls = []
    class Response:
        status_code = 202
        def raise_for_status(self): pass
    class Client:
        def __init__(self, **options): assert options["follow_redirects"] is False
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def post(self, url, *, content, headers): calls.append((url, content, headers)); return Response()
    monkeypatch.setattr("app.workflow_automation.httpx.Client", Client)
    monkeypatch.setattr("app.workflow_automation.socket.getaddrinfo", lambda *args, **kwargs: [(None, None, None, None, ("93.184.216.34", 443))])
    sender = SignedWebhook(OutboundPolicy(url="https://hooks.example.com/events", allowed_hosts=("hooks.example.com",), secret="test-secret", timeout_seconds=3))
    event = build_integration_event("request.approved", tenant_id="acme", resource_id="r1", summary="采购合同已批准")
    result = sender.send(event, idempotency_key="event-1")
    assert result == {"delivered": True, "status": 202, "idempotencyKey": "event-1"}
    assert calls[0][2]["Idempotency-Key"] == "event-1"
    assert calls[0][2]["X-CRA-Signature"].startswith("sha256=")
    assert b"test-secret" not in calls[0][1]


def test_integration_event_rejects_contract_body_and_supports_required_kinds():
    for kind in ["enterprise_im", "ticket", "procurement_crm", "electronic_signature"]:
        event = build_integration_event("obligation.due", tenant_id="a", resource_id="x", summary="续签到期", integration_kind=kind)
        assert event["integrationKind"] == kind
    with pytest.raises(ValueError, match="summary"):
        build_integration_event("x", tenant_id="a", resource_id="x", summary="a" * 501)


def test_dispatcher_records_only_delivery_metadata(tmp_path):
    class Sender:
        def send(self, event, *, idempotency_key):
            assert "contractText" not in event
            return {"delivered": True, "status": 202, "idempotencyKey": idempotency_key}
    dispatcher = IntegrationDispatcher(tmp_path / "delivery.db", {"enterprise_im": Sender()})
    event = build_integration_event("request.approved", tenant_id="a", resource_id="r1", summary="审批通过", integration_kind="enterprise_im")
    result = dispatcher.dispatch(event, idempotency_key="event-1")
    assert result["delivered"] is True
    attempts = dispatcher.attempts(tenant_id="a")
    assert attempts[0]["eventType"] == "request.approved"
    assert "summary" not in attempts[0]
    assert "secret" not in str(attempts[0]).lower()
    assert dispatcher.dispatch(event, idempotency_key="event-1")["duplicate"] is True


def test_dispatcher_retries_transient_failures_and_marks_failed_delivery_for_alerting(tmp_path):
    calls = []

    class Sender:
        def send(self, event, *, idempotency_key):
            calls.append(idempotency_key)
            if len(calls) < 3:
                raise RuntimeError("temporary upstream outage")
            return {"delivered": True, "status": 202, "idempotencyKey": idempotency_key}

    dispatcher = IntegrationDispatcher(tmp_path / "delivery.db", {"enterprise_im": Sender()}, max_attempts=3, retry_backoff_seconds=0)
    event = build_integration_event("request.approved", tenant_id="a", resource_id="r1", summary="审批通过", integration_kind="enterprise_im")
    result = dispatcher.dispatch(event, idempotency_key="event-retry")
    assert result["delivered"] is True and result["attemptCount"] == 3 and len(calls) == 3
    assert dispatcher.attempts(tenant_id="a")[0]["attemptCount"] == 3

    class AlwaysFail:
        def send(self, event, *, idempotency_key):
            raise RuntimeError("permanent upstream outage")

    failed = IntegrationDispatcher(tmp_path / "failed.db", {"enterprise_im": AlwaysFail()}, max_attempts=2, retry_backoff_seconds=0)
    failed_result = failed.dispatch(event, idempotency_key="event-failed")
    assert failed_result["delivered"] is False and failed_result["alert"] is True and failed_result["attemptCount"] == 2
    assert failed.attempts(tenant_id="a")[0]["alert"] is True


def test_integration_config_store_dispatches_enabled_tenant_config_without_exposing_secret(tmp_path, monkeypatch):
    store = IntegrationConfigStore(tmp_path / "integrations.db")
    store.configure(tenant_id="acme", integration_kind="enterprise_im", url="https://hooks.example.com/events", allowed_hosts=["hooks.example.com"], secret="test-secret", enabled=True)
    configs = store.list_configs(tenant_id="acme")
    assert configs == [{"integrationKind": "enterprise_im", "url": "https://hooks.example.com/events", "allowedHosts": ["hooks.example.com"], "timeoutSeconds": 5.0, "enabled": True, "hasSecret": True, "updatedAt": configs[0]["updatedAt"]}]
    assert "test-secret" not in str(configs)

    sent = []
    monkeypatch.setattr("app.workflow_automation.SignedWebhook.send", lambda self, event, *, idempotency_key: sent.append((event, idempotency_key)) or {"delivered": True, "status": 202, "idempotencyKey": idempotency_key})
    event = build_integration_event("request.approved", tenant_id="acme", resource_id="r1", summary="状态更新")
    result = store.dispatch(event, idempotency_key="event-1")
    assert result["delivered"] is True and sent[0][0]["integrationKind"] == "enterprise_im"
