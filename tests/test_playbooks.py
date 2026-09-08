from __future__ import annotations

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import sqlite3
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.playbook_store import PlaybookStore
from app.playbooks import PlaybookContent, PlaybookRule, PlaybookSnapshot, content_hash


def make_rule(rule_id: str = "payment-term") -> PlaybookRule:
    return PlaybookRule(
        id=rule_id,
        categoryId="payment",
        riskType="late_payment",
        severity="high",
        triggerCondition="Payment exceeds 60 days.",
        reviewQuestion="Is the payment term at most 60 days?",
        acceptableCondition="Payment is due within 60 days.",
        suggestedClause="Payment is due within 30 days of invoice.",
        escalationPolicy="Escalate terms longer than 60 days.",
    )


def make_content(*, rules: tuple[PlaybookRule, ...] | None = None) -> PlaybookContent:
    return PlaybookContent(
        name="China purchase baseline",
        contractType="purchase",
        jurisdiction="CN",
        businessScenario="standard",
        effectiveScope=("procurement", "east-region"),
        rules=(make_rule(),) if rules is None else rules,
    )


def test_domain_models_validate_and_are_deeply_immutable() -> None:
    content = make_content()

    assert content.model_dump(mode="json") == {
        "name": "China purchase baseline",
        "contractType": "purchase",
        "jurisdiction": "CN",
        "businessScenario": "standard",
        "effectiveScope": ["procurement", "east-region"],
        "rules": [
            {
                "id": "payment-term",
                "categoryId": "payment",
                "riskType": "late_payment",
                "severity": "high",
                "triggerCondition": "Payment exceeds 60 days.",
                "reviewQuestion": "Is the payment term at most 60 days?",
                "acceptableCondition": "Payment is due within 60 days.",
                "suggestedClause": "Payment is due within 30 days of invoice.",
                "escalationPolicy": "Escalate terms longer than 60 days.",
            }
        ],
    }
    with pytest.raises(ValidationError):
        content.name = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        content.rules[0].severity = "low"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("contractType", "  "),
        ("businessScenario", ""),
        ("effectiveScope", ()),
        ("effectiveScope", ("procurement", " ")),
    ],
)
def test_content_rejects_invalid_scope_fields(field: str, value: object) -> None:
    data = make_content().model_dump(mode="python")
    data[field] = value

    with pytest.raises(ValidationError):
        PlaybookContent.model_validate(data)


def test_content_rejects_duplicate_rule_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate rule id"):
        make_content(rules=(make_rule("same"), make_rule("same")))


def test_domain_models_reject_unknown_fields_and_invalid_snapshot_versions() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        PlaybookRule.model_validate({**make_rule().model_dump(), "typoField": "ignored"})

    content = make_content()
    snapshot = {
        "playbookId": "book",
        "tenantId": "acme",
        "version": True,
        "content": content.model_dump(mode="json"),
        "contentHash": content_hash(content),
        "createdAt": "2026-09-07T00:00:00+00:00",
        "createdBy": "alice",
        "publishedAt": "2026-09-07T00:01:00+00:00",
        "publishedBy": "alice",
    }
    with pytest.raises(ValidationError):
        PlaybookSnapshot.model_validate(snapshot)


def test_snapshot_rejects_empty_rules_even_though_draft_content_allows_them() -> None:
    content = make_content(rules=())

    with pytest.raises(ValidationError, match="without rules"):
        PlaybookSnapshot(
            playbookId="book",
            tenantId="acme",
            version=1,
            content=content,
            contentHash=content_hash(content),
            createdAt="2026-09-07T00:00:00+00:00",
            createdBy="alice",
            publishedAt="2026-09-07T00:01:00+00:00",
            publishedBy="alice",
        )


def test_draft_publish_archive_lifecycle_is_audited_and_snapshot_is_immutable(tmp_path: Path) -> None:
    path = tmp_path / "playbooks.db"
    store = PlaybookStore(path)

    draft = store.create_draft(make_content(rules=()), tenant_id="acme", subject="alice")
    assert set(draft) == {
        "playbookId",
        "tenantId",
        "version",
        "status",
        "content",
        "contentHash",
        "createdAt",
        "createdBy",
        "updatedAt",
        "updatedBy",
        "publishedAt",
        "publishedBy",
        "archivedAt",
        "archivedBy",
        "snapshot",
    }
    assert draft["version"] == 1
    assert draft["status"] == "draft"
    assert draft["contentHash"] is None
    assert draft["snapshot"] is None

    updated = store.update_draft(
        draft["playbookId"], 1, make_content(), tenant_id="acme", subject="bob"
    )
    active = store.publish(draft["playbookId"], 1, tenant_id="acme", subject="publisher")
    snapshot = PlaybookSnapshot.model_validate(active["snapshot"])
    assert active["status"] == "active"
    assert active["contentHash"] == snapshot.contentHash
    assert snapshot.content == make_content()
    assert snapshot.createdAt == draft["createdAt"]
    assert snapshot.createdBy == "alice"
    assert snapshot.publishedBy == "publisher"

    with pytest.raises(ValueError, match="only draft"):
        store.update_draft(
            draft["playbookId"], 1, make_content(), tenant_id="acme", subject="bob"
        )

    archived = store.archive(draft["playbookId"], 1, tenant_id="acme", subject="archiver")
    assert archived["status"] == "archived"
    assert archived["content"] == updated["content"]
    assert archived["snapshot"] == active["snapshot"]
    assert PlaybookStore(path).get_version(draft["playbookId"], 1, tenant_id="acme") == archived


def test_archived_playbook_versions_can_be_retained_by_identifier(tmp_path: Path) -> None:
    store = PlaybookStore(tmp_path / "playbooks.db")
    draft = store.create_draft(make_content(), tenant_id="acme", subject="alice")
    store.publish(draft["playbookId"], 1, tenant_id="acme", subject="alice")
    archived = store.archive(draft["playbookId"], 1, tenant_id="acme", subject="alice")
    candidates = store.retention_candidates(tenant_id="acme", before="2999-01-01T00:00:00+00:00")
    assert candidates[0]["resourceId"] == f"{archived['playbookId']}:1"
    assert store.purge_version(archived["playbookId"], 1, tenant_id="acme") is True
    assert store.get_version(archived["playbookId"], 1, tenant_id="acme") is None
    assert store.list_events(draft["playbookId"], tenant_id="acme") == []


def test_new_versions_preserve_published_content_and_allocate_unique_numbers(tmp_path):
    store = PlaybookStore(tmp_path / "books.db")
    book = store.create_draft(make_content(), tenant_id="acme", subject="alice")
    book_id = book["playbookId"]
    active = store.publish(book_id, 1, tenant_id="acme", subject="alice")
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(store.create_version, book_id, source_version=1, tenant_id="acme", subject="editor") for _ in range(4)]
        drafts = [future.result() for future in futures]
    assert sorted(draft["version"] for draft in drafts) == [2, 3, 4, 5]
    assert all(draft["status"] == "draft" and draft["snapshot"] is None for draft in drafts)
    changed = make_content().model_dump(mode="json")
    changed["rules"][0]["suggestedClause"] = "Revised replacement."
    store.update_draft(book_id, 2, PlaybookContent.model_validate(changed), tenant_id="acme", subject="editor")
    v2 = store.publish(book_id, 2, tenant_id="acme", subject="publisher")
    assert v2["contentHash"] != active["contentHash"]
    assert store.get_version(book_id, 1, tenant_id="acme") == active
    assert [v["version"] for v in store.list_versions(book_id, tenant_id="acme")] == [5, 4, 3, 2, 1]
    assert [v["version"] for v in store.list_playbooks(tenant_id="acme")] == [5]


def test_published_snapshot_resolves_only_in_its_active_scope(tmp_path):
    store = PlaybookStore(tmp_path / "books.db")
    draft = store.create_draft(make_content(), tenant_id="acme", subject="alice")
    book_id = draft["playbookId"]
    scope = dict(tenant_id="acme", contract_type="purchase", jurisdiction="CN", business_scenario="standard", effective_scope="procurement")
    with pytest.raises(ValueError, match="active"):
        store.resolve_snapshot(book_id, 1, **scope)
    published = store.publish(book_id, 1, tenant_id="acme", subject="alice")
    assert store.resolve_snapshot(book_id, 1, **scope).model_dump(mode="json") == published["snapshot"]
    for field, value in [("contract_type", "sale"), ("jurisdiction", "US"), ("business_scenario", "nonstandard"), ("effective_scope", "elsewhere")]:
        with pytest.raises(ValueError, match="scope"):
            store.resolve_snapshot(book_id, 1, **{**scope, field: value})
    store.archive(book_id, 1, tenant_id="acme", subject="alice")
    with pytest.raises(ValueError, match="active"):
        store.resolve_snapshot(book_id, 1, **scope)
    assert store.create_version(book_id, source_version=1, tenant_id="acme", subject="alice")["version"] == 2


def test_scope_wildcard_does_not_disable_tenant_or_contract_check(tmp_path):
    store = PlaybookStore(tmp_path / "books.db")
    data = make_content().model_dump(mode="json")
    data["effectiveScope"] = ["*"]
    book = store.create_draft(PlaybookContent.model_validate(data), tenant_id="acme", subject="alice")
    store.publish(book["playbookId"], 1, tenant_id="acme", subject="alice")
    scope = dict(tenant_id="acme", contract_type="purchase", jurisdiction="CN", business_scenario="standard", effective_scope="any-business-unit")
    assert store.resolve_snapshot(book["playbookId"], 1, **scope).version == 1
    with pytest.raises(KeyError):
        store.resolve_snapshot(book["playbookId"], 1, **{**scope, "tenant_id": "other"})
    with pytest.raises(ValueError):
        store.resolve_snapshot(book["playbookId"], 1, **{**scope, "contract_type": "sale"})


def test_tenant_isolation_for_all_store_operations(tmp_path):
    store = PlaybookStore(tmp_path / "books.db")
    book = store.create_draft(make_content(), tenant_id="acme", subject="alice")
    book_id = book["playbookId"]
    assert store.get_version(book_id, 1, tenant_id="other") is None
    assert store.list_playbooks(tenant_id="other") == []
    assert store.list_versions(book_id, tenant_id="other") == []
    assert store.list_events(book_id, tenant_id="other") == []
    for operation in [
        lambda: store.update_draft(book_id, 1, make_content(), tenant_id="other", subject="mallory"),
        lambda: store.publish(book_id, 1, tenant_id="other", subject="mallory"),
        lambda: store.archive(book_id, 1, tenant_id="other", subject="mallory"),
        lambda: store.create_version(book_id, source_version=1, tenant_id="other", subject="mallory"),
    ]:
        with pytest.raises(KeyError):
            operation()
    assert len(store.list_events(book_id, tenant_id="acme")) == 1


def test_invalid_lifecycle_does_not_add_events(tmp_path):
    store = PlaybookStore(tmp_path / "books.db")
    draft = store.create_draft(make_content(rules=()), tenant_id="acme", subject="alice")
    book_id = draft["playbookId"]
    with pytest.raises(ValueError, match="without rules"):
        store.publish(book_id, 1, tenant_id="acme", subject="alice")
    store.archive(book_id, 1, tenant_id="acme", subject="alice")
    for operation in [
        lambda: store.publish(book_id, 1, tenant_id="acme", subject="alice"),
        lambda: store.archive(book_id, 1, tenant_id="acme", subject="alice"),
        lambda: store.update_draft(book_id, 1, make_content(), tenant_id="acme", subject="alice"),
    ]:
        with pytest.raises(ValueError):
            operation()
    assert [e["type"] for e in store.list_events(book_id, tenant_id="acme")] == ["created", "archived"]


def test_snapshot_detects_content_tampering_and_reads_return_detached_data(tmp_path):
    store = PlaybookStore(tmp_path / "books.db")
    book = store.create_draft(make_content(), tenant_id="acme", subject="alice")
    active = store.publish(book["playbookId"], 1, tenant_id="acme", subject="alice")
    expected = copy.deepcopy(active)
    active["content"]["rules"][0]["severity"] = "low"
    assert store.get_version(book["playbookId"], 1, tenant_id="acme") == expected
    forged = copy.deepcopy(expected["snapshot"])
    forged["content"]["rules"][0]["severity"] = "low"
    with pytest.raises(ValueError, match="hash"):
        PlaybookSnapshot.model_validate(forged)
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE playbook_versions SET snapshot_json = ?", (json.dumps(forged),))
    with pytest.raises(ValueError, match="hash"):
        store.get_version(book["playbookId"], 1, tenant_id="acme")


@pytest.mark.parametrize("field,value", [("tenantId", "other"), ("version", 99), ("playbookId", "another-book"), ("publishedBy", "forged")])
def test_stored_snapshot_identity_must_match_its_version_record(tmp_path, field, value):
    store = PlaybookStore(tmp_path / "books.db")
    draft = store.create_draft(make_content(), tenant_id="acme", subject="alice")
    active = store.publish(draft["playbookId"], 1, tenant_id="acme", subject="alice")
    forged = active["snapshot"]
    forged[field] = value
    with sqlite3.connect(store.path) as conn:
        conn.execute("UPDATE playbook_versions SET snapshot_json = ?", (json.dumps(forged),))
    with pytest.raises(ValueError, match="match"):
        store.get_version(draft["playbookId"], 1, tenant_id="acme")


def test_store_revalidates_unchecked_model_copies_before_persisting(tmp_path):
    store = PlaybookStore(tmp_path / "books.db")
    forged = make_content().model_copy(update={"name": ""})
    with pytest.raises(ValueError):
        store.create_draft(forged, tenant_id="acme", subject="alice")
    assert store.list_playbooks(tenant_id="acme") == []
