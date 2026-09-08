from threading import Event, Thread

import pytest

from app.data_lifecycle import DataLifecycleStore


def test_legal_hold_is_tenant_scoped_and_append_only_audited(tmp_path):
    store = DataLifecycleStore(tmp_path / "lifecycle.db")
    hold = store.place_hold(tenant_id="acme", resource_type="asset", resource_id="a1", reason="诉讼保全", actor="alice")
    assert hold["resourceId"] == "a1" and store.is_held("acme", "asset", "a1")
    assert not store.is_held("other", "asset", "a1")
    assert store.release_hold("acme", "asset", "a1", actor="alice")["releasedAt"]
    assert not store.is_held("acme", "asset", "a1")
    events = store.events("acme")
    assert [item["action"] for item in events] == ["hold.created", "hold.released"]


def test_purge_excludes_held_and_foreign_resources_and_supports_dry_run(tmp_path):
    store = DataLifecycleStore(tmp_path / "lifecycle.db")
    store.place_hold(tenant_id="acme", resource_type="asset", resource_id="held", reason="保全", actor="alice")
    records = [
        {"resourceType": "asset", "resourceId": "old", "tenantId": "acme", "createdAt": "2025-01-01T00:00:00+00:00"},
        {"resourceType": "asset", "resourceId": "held", "tenantId": "acme", "createdAt": "2025-01-01T00:00:00+00:00"},
        {"resourceType": "asset", "resourceId": "foreign", "tenantId": "other", "createdAt": "2025-01-01T00:00:00+00:00"},
    ]
    deleted = []
    preview = store.purge(records, tenant_id="acme", before="2026-01-01T00:00:00+00:00", actor="admin", dry_run=True, delete=lambda item: deleted.append(item))
    assert preview == {"eligible": ["old"], "held": ["held"], "deleted": []}
    assert deleted == []
    result = store.purge(records, tenant_id="acme", before="2026-01-01T00:00:00+00:00", actor="admin", delete=lambda item: deleted.append(item))
    assert result == {"eligible": ["old"], "held": ["held"], "deleted": ["old"]}
    assert [item["resourceId"] for item in deleted] == ["old"]


@pytest.mark.parametrize("before", ["not-a-date", "2026-01-01T00:00:00"])
def test_purge_requires_timezone_aware_cutoff(tmp_path, before):
    store = DataLifecycleStore(tmp_path / "lifecycle.db")
    with pytest.raises(ValueError, match="timezone|ISO"):
        store.purge([], tenant_id="acme", before=before, actor="admin", dry_run=True, delete=lambda _: None)


def test_purge_serializes_hold_creation_with_deletion(tmp_path):
    store = DataLifecycleStore(tmp_path / "lifecycle.db")
    records = [{"resourceType": "asset", "resourceId": "a1", "tenantId": "acme", "createdAt": "2025-01-01T00:00:00+00:00"}]
    delete_started, hold_done = Event(), Event()
    hold_during_delete = []

    def place_hold():
        store.place_hold(tenant_id="acme", resource_type="asset", resource_id="a1", reason="并发保全", actor="legal")
        hold_done.set()

    def delete(_record):
        delete_started.set()
        holder = Thread(target=place_hold)
        holder.start()
        hold_during_delete.append(hold_done.wait(0.1))
        holder.join(timeout=1)

    purge_thread = Thread(target=lambda: store.purge(records, tenant_id="acme", before="2026-01-01T00:00:00+00:00", actor="admin", delete=delete))
    purge_thread.start()
    assert delete_started.wait(1)
    purge_thread.join(timeout=2)
    assert not purge_thread.is_alive()
    assert hold_during_delete == [False]
