import pytest

from app.visitor_sessions import VisitorSessionManager


def test_visitor_token_round_trips_and_isolates_tenants():
    manager = VisitorSessionManager("test-signing-secret", ttl_seconds=60)

    first_token, first = manager.issue(now=1000)
    second_token, second = manager.issue(now=1000)

    assert manager.authenticate(first_token, now=1001) == first
    assert first.role == "visitor"
    assert first.tenant_id.startswith("visitor-")
    assert second.tenant_id != first.tenant_id
    assert second_token != first_token


def test_visitor_token_rejects_tampering_and_expiry():
    manager = VisitorSessionManager("test-signing-secret", ttl_seconds=60)
    token, _ = manager.issue(now=1000)

    with pytest.raises(PermissionError, match="invalid visitor session"):
        manager.authenticate(token[:-1] + ("A" if token[-1] != "A" else "B"), now=1001)
    with pytest.raises(PermissionError, match="visitor session expired"):
        manager.authenticate(token, now=1061)
