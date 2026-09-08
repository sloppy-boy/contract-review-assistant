from datetime import UTC, datetime, timedelta

import pytest

from app.asset_store import AssetStore
from app.security import Principal


ALICE = Principal("alice", "acme", "legal_reviewer")
BOB = Principal("bob", "acme", "reader")
ADMIN = Principal("admin", "acme", "admin")
OTHER = Principal("mallory", "other", "admin")


def extracted():
    return {
        "text": "付款期限为收到发票后30日。自动续约须提前60日通知。",
        "segments": [
            {"id": "p1", "location": {"page": 1}, "text": "付款期限为收到发票后30日。"},
            {"id": "p2", "location": {"page": 2}, "text": "自动续约须提前60日通知。"},
        ],
        "metadata": {"partyCandidates": [{"value": "甲方", "source": "p1"}]},
        "warnings": ["synthetic"],
        "method": "text",
    }


def make_store(tmp_path):
    return AssetStore(tmp_path / "assets.db")


def test_persists_original_and_extraction_without_leaking_body_in_list(tmp_path):
    store = make_store(tmp_path)
    asset = store.create(" 合同.txt ", b"original\x00bytes", extracted(), actor=ALICE, tags=["采购"])

    reopened = make_store(tmp_path)
    assert reopened.original(asset["id"], actor=ALICE) == ("合同.txt", b"original\x00bytes")
    assert reopened.get(asset["id"], actor=ALICE)["segments"][1]["location"] == {"page": 2}
    summary = reopened.list_assets(actor=ALICE)[0]
    assert "text" not in summary and "segments" not in summary
    assert summary["revision"] == 1


def test_tenant_and_private_acl_and_write_rules(tmp_path):
    store = make_store(tmp_path)
    private = store.create("a.txt", b"a", extracted(), actor=ALICE, shared_with=["bob"])
    tenant = store.create("b.txt", b"b", extracted(), actor=ALICE, visibility="tenant")

    assert store.get(private["id"], actor=BOB)["id"] == private["id"]
    assert store.get(private["id"], actor=ADMIN)["id"] == private["id"]
    assert store.get(tenant["id"], actor=BOB)["id"] == tenant["id"]
    assert [row["id"] for row in store.list_assets(actor=OTHER)] == []
    with pytest.raises(KeyError):
        store.get(private["id"], actor=OTHER)
    with pytest.raises(PermissionError):
        store.update(private["id"], actor=BOB, expected_revision=1, title="x", tags=[], visibility="private", shared_with=[])


def test_update_checks_revision_atomically_and_returns_current_asset(tmp_path):
    store = make_store(tmp_path)
    asset = store.create("a.txt", b"a", extracted(), actor=ALICE)
    updated = store.update(asset["id"], actor=ALICE, expected_revision=1, title=" 新标题 ", tags=["重要"], visibility="tenant", shared_with=[])
    assert updated["title"] == "新标题" and updated["revision"] == 2
    with pytest.raises(ValueError, match="revision"):
        store.update(asset["id"], actor=ALICE, expected_revision=1, title="lost", tags=[], visibility="tenant", shared_with=[])
    assert store.get(asset["id"], actor=ALICE)["title"] == "新标题"


def test_search_and_ask_return_real_segment_citations_only(tmp_path):
    store = make_store(tmp_path)
    asset = store.create("a.txt", b"a", extracted(), actor=ALICE, visibility="tenant")
    hidden = store.create("secret.txt", b"s", {**extracted(), "segments": [{"id": "s1", "location": "secret", "text": "机密付款"}], "text": "机密付款"}, actor=OTHER)

    hits = store.search("付款 发票", actor=BOB)
    assert hits[0] == {"assetId": asset["id"], "title": "a.txt", "segmentId": "p1", "location": {"page": 1}, "text": "付款期限为收到发票后30日。", "score": 2.0}
    assert all(hit["assetId"] != hidden["id"] for hit in hits)
    answer = store.ask("付款期限", actor=BOB)
    assert answer["mode"] == "extractive" and answer["citations"][0]["segmentId"] == "p1"
    assert "30日" in answer["answer"]
    natural = store.ask("自动续约何时通知？", actor=BOB)
    assert natural["citations"][0]["segmentId"] == "p2"
    assert natural["answer"] == "自动续约须提前60日通知。"
    unsupported = store.ask("争议管辖法院", actor=BOB)
    assert unsupported == {"answer": "无法从可访问合同原文中找到依据。", "citations": [], "mode": "extractive"}


def test_obligation_lifecycle_and_reminders_are_recipient_scoped_and_deduplicated(tmp_path):
    store = make_store(tmp_path)
    asset = store.create("a.txt", b"a", extracted(), actor=ALICE, shared_with=["bob"])
    due = datetime.now(UTC) - timedelta(minutes=1)
    changed = store.add_obligation(asset["id"], actor=ALICE, expected_revision=1, title=" 付款 ", due_at=due, kind="obligation")
    obligation = store.obligations(asset["id"], actor=BOB)[0]
    assert changed["revision"] == 2
    assert obligation["dueAt"] == due.isoformat() and obligation["completedAt"] is None
    with pytest.raises(PermissionError):
        store.add_obligation(asset["id"], actor=BOB, expected_revision=2, title="越权", due_at=due, kind="obligation")
    with pytest.raises(PermissionError):
        store.complete_obligation(asset["id"], obligation["id"], actor=BOB, expected_revision=2)
    assert store.get(asset["id"], actor=ALICE)["revision"] == 2

    assert len(store.dispatch_reminders(actor=BOB, now=datetime.now(UTC))) == 1
    assert len(store.dispatch_reminders(actor=BOB, now=datetime.now(UTC))) == 0
    note = store.notifications(actor=BOB)[0]
    assert note["recipient"] == "bob" and note["assetId"] == asset["id"] and note["readAt"] is None
    assert store.mark_read(note["id"], actor=BOB)["readAt"].endswith("+00:00")
    with pytest.raises(KeyError):
        store.mark_read(note["id"], actor=ALICE)

    # Removing access also removes the reminder from notification reads.
    current = store.get(asset["id"], actor=ALICE)
    store.update(asset["id"], actor=ALICE, expected_revision=current["revision"], title=current["title"], tags=[], visibility="private", shared_with=[])
    assert store.notifications(actor=BOB) == []
    with pytest.raises(KeyError):
        store.mark_read(note["id"], actor=BOB)

    current = store.get(asset["id"], actor=ALICE)
    completed = store.complete_obligation(asset["id"], obligation["id"], actor=ALICE, expected_revision=current["revision"])
    assert completed["revision"] == current["revision"] + 1
    assert store.obligations(asset["id"], actor=ALICE)[0]["completedAt"] is not None


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_create_rejects_invalid_names_without_side_effect(tmp_path, bad):
    store = make_store(tmp_path)
    with pytest.raises((TypeError, ValueError)):
        store.create(bad, b"x", extracted(), actor=ALICE)
    assert store.list_assets(actor=ALICE) == []


def test_validation_rejects_bad_visibility_dates_kinds_and_limits(tmp_path):
    store = make_store(tmp_path)
    with pytest.raises(ValueError):
        store.create("a.txt", b"x", extracted(), actor=ALICE, visibility="public")
    with pytest.raises(ValueError):
        store.create("a.txt", b"x" * (10 * 1024 * 1024 + 1), extracted(), actor=ALICE)
    asset = store.create("a.txt", b"x", extracted(), actor=ALICE, visibility="tenant")
    with pytest.raises(ValueError):
        store.add_obligation(asset["id"], actor=ALICE, expected_revision=1, title="x", due_at=datetime.now(), kind="obligation")
    with pytest.raises(ValueError):
        store.add_obligation(asset["id"], actor=ALICE, expected_revision=1, title="x", due_at=datetime.now(UTC), kind="memo")


def test_iso_due_dates_nested_paths_and_body_query_are_supported(tmp_path):
    store = AssetStore(tmp_path / "nested" / "data" / "assets.db")
    asset = store.create("a.txt", b"x", extracted(), actor=ALICE, visibility="tenant")
    updated = store.add_obligation(asset["id"], actor=ALICE, expected_revision=1, title="付款", due_at="2030-01-01T08:00:00+08:00", kind="key_date")
    assert updated["revision"] == 2
    assert store.obligations(asset["id"], actor=ALICE)[0]["dueAt"] == "2030-01-01T00:00:00+00:00"
    assert store.list_assets(actor=ALICE, query="自动续约")[0]["id"] == asset["id"]


def test_owner_gets_reminder_when_admin_created_private_asset_obligation(tmp_path):
    store = make_store(tmp_path)
    asset = store.create("private.txt", b"x", extracted(), actor=ALICE)
    store.add_obligation(
        asset["id"], actor=ADMIN, expected_revision=1, title="管理员录入的到期项",
        due_at=datetime.now(UTC) - timedelta(seconds=1), kind="key_date",
    )

    assert store.get(asset["id"], actor=ALICE)["createdBy"] == "alice"
    assert len(store.dispatch_reminders(actor=ALICE)) == 1
    assert len(store.dispatch_reminders(actor=ADMIN)) == 1


def test_unknown_roles_revisions_and_segment_shapes_are_rejected(tmp_path):
    store = make_store(tmp_path)
    stranger = Principal("x", "acme", "superuser")
    with pytest.raises(PermissionError):
        store.create("a.txt", b"x", extracted(), actor=stranger)
    asset = store.create("a.txt", b"x", extracted(), actor=ALICE, visibility="tenant")
    with pytest.raises(PermissionError):
        store.update(asset["id"], actor=stranger, expected_revision=1, title="x", tags=[], visibility="private", shared_with=[])
    with pytest.raises(ValueError):
        store.update(asset["id"], actor=ALICE, expected_revision=True, title="x", tags=[], visibility="private", shared_with=[])
    with pytest.raises(ValueError):
        store.add_obligation(asset["id"], actor=ALICE, expected_revision=True, title="x", due_at=datetime.now(UTC), kind="obligation")
    duplicate = extracted()
    duplicate["segments"] = [duplicate["segments"][0], duplicate["segments"][0]]
    with pytest.raises(ValueError, match="segment"):
        store.create("duplicate.txt", b"x", duplicate, actor=ALICE)
    missing_location = extracted()
    missing_location["segments"][0]["location"] = {}
    with pytest.raises(ValueError, match="location"):
        store.create("location.txt", b"x", missing_location, actor=ALICE)


def test_soft_delete_hides_asset_and_purge_removes_only_deleted_asset(tmp_path):
    store = make_store(tmp_path)
    asset = store.create("a.txt", b"x", extracted(), actor=ALICE)
    assert store.soft_delete(asset["id"], actor=ALICE, reason="保留期到期")["deletedAt"]
    assert store.list_assets(actor=ALICE) == []
    with pytest.raises(KeyError):
        store.get(asset["id"], actor=ALICE)
    assert store.restore(asset["id"], actor=ALICE)["id"] == asset["id"]
    assert store.get(asset["id"], actor=ALICE)["id"] == asset["id"]


def test_soft_delete_requires_owner_or_admin_and_purge_candidates_are_metadata_only(tmp_path):
    store = make_store(tmp_path)
    asset = store.create("a.txt", b"x", extracted(), actor=ALICE, visibility="tenant")
    with pytest.raises(PermissionError):
        store.soft_delete(asset["id"], actor=BOB, reason="越权")
    deleted = store.soft_delete(asset["id"], actor=ADMIN, reason="清理")
    candidates = store.deleted_candidates(tenant_id="acme", before="2999-01-01T00:00:00+00:00")
    assert candidates[0]["resourceId"] == asset["id"] and "text" not in candidates[0]
    store.purge_deleted(asset["id"], tenant_id="acme")
    assert store.deleted_candidates(tenant_id="acme", before="2999-01-01T00:00:00+00:00") == []
