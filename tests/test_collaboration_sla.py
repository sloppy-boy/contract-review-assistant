from datetime import UTC, datetime
from app.collaboration import CollaborationStore, RequestContent
from app.security import Principal


def test_sla_notifications_are_multi_level_idempotent_and_audited(tmp_path):
    store = CollaborationStore(tmp_path / 'c.db', clock=lambda: datetime(2026, 9, 1, tzinfo=UTC))
    owner = Principal('owner', 'a', 'requester')
    request = store.create(RequestContent(title='x', contractType='purchase'), actor=owner)
    request = store.submit(request['id'], actor=owner, expected_revision=1)
    request = store.assign(request['id'], actor=owner, expected_revision=2, assignee='reviewer', approver='approver', due_at='2026-09-02T00:00:00Z')
    assert len(store.dispatch_sla_reminders(tenant_id='a', admin_subjects=['admin'], now=datetime(2026, 9, 1, 12, tzinfo=UTC))) == 2
    assert store.dispatch_sla_reminders(tenant_id='a', admin_subjects=['admin'], now=datetime(2026, 9, 1, 12, tzinfo=UTC)) == []
    assert len(store.dispatch_sla_reminders(tenant_id='a', admin_subjects=['admin'], now=datetime(2026, 9, 2, tzinfo=UTC))) == 2
    escalated = store.dispatch_sla_reminders(tenant_id='a', admin_subjects=['admin'], now=datetime(2026, 9, 3, tzinfo=UTC))
    assert {note['recipient'] for note in escalated} == {'owner', 'approver', 'admin'}
    events = store.events(request['id'], actor=owner)
    assert [event['type'] for event in events].count('sla_overdue_escalation') == 1
