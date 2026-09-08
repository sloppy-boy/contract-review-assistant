import hashlib

import pytest

from app.asset_store import AssetStore
from app.security import Principal


OWNER = Principal('owner', 'a', 'requester')
READER = Principal('reader', 'a', 'reader')
OTHER = Principal('other', 'b', 'admin')


def extracted(*paragraphs):
    return {'text': '\n'.join(paragraphs), 'segments': [
        {'id': f'p{i}', 'location': {'paragraph': i}, 'text': text}
        for i, text in enumerate(paragraphs, 1)
    ], 'metadata': {'candidate': True}, 'warnings': ['synthetic'], 'method': 'text'}


def test_versions_preserve_bytes_source_and_optimistic_revision(tmp_path):
    store = AssetStore(tmp_path / 'assets.db')
    first = store.create('old.txt', b'old bytes', extracted('Payment 30 days', 'Keep'), actor=OWNER)
    frozen = store.get_version(first['id'], 1, actor=OWNER)
    second = store.add_version(first['id'], 'new.txt', b'new bytes', extracted('Payment 60 days', 'Keep'), actor=OWNER, expected_revision=1)
    assert second['version'] == 2 and second['parentVersion'] == 1 and second['revision'] == 2
    assert second['contentHash'] == hashlib.sha256(b'new bytes').hexdigest()
    reopened = AssetStore(tmp_path / 'assets.db')
    assert reopened.get_version(first['id'], 1, actor=OWNER) == frozen
    assert reopened.original(first['id'], actor=OWNER, version=1) == ('old.txt', b'old bytes')
    assert reopened.original(first['id'], actor=OWNER) == ('new.txt', b'new bytes')
    assert [v['version'] for v in reopened.versions(first['id'], actor=OWNER)] == [2, 1]
    with pytest.raises(ValueError, match='revision'):
        store.add_version(first['id'], 'stale.txt', b'stale', extracted('Stale'), actor=OWNER, expected_revision=1)
    assert len(store.versions(first['id'], actor=OWNER)) == 2


def test_all_version_reads_and_writes_follow_current_asset_acl(tmp_path):
    store = AssetStore(tmp_path / 'assets.db')
    first = store.create('a.txt', b'a', extracted('Payment'), actor=OWNER, shared_with=['reader'])
    assert store.get_version(first['id'], 1, actor=READER)['text'] == 'Payment'
    with pytest.raises(PermissionError):
        store.add_version(first['id'], 'a.txt', b'b', extracted('Other'), actor=READER, expected_revision=1)
    store.update(first['id'], actor=OWNER, expected_revision=1, title='a', tags=[], visibility='private', shared_with=[])
    for actor in (READER, OTHER):
        for operation in (
            lambda: store.versions(first['id'], actor=actor),
            lambda: store.get_version(first['id'], 1, actor=actor),
            lambda: store.original(first['id'], actor=actor, version=1),
            lambda: store.diff(first['id'], 1, 1, actor=actor),
        ):
            with pytest.raises(KeyError):
                operation()


def test_diff_and_risk_impact_have_stable_references_and_conservative_mapping(tmp_path):
    store = AssetStore(tmp_path / 'assets.db')
    first = store.create('a.txt', b'a', extracted('Keep', 'Payment 30 days', 'Anchor', 'Deleted obligation', 'End'), actor=OWNER)
    store.add_version(first['id'], 'b.txt', b'b', extracted('Keep', 'Payment 60 days', 'Anchor', 'End', 'New duty'), actor=OWNER, expected_revision=1)
    report = {'risks': [
        {'id': 'keep', 'clauseId': 'p1', 'quote': 'Keep'},
        {'id': 'modify', 'clauseId': 'p2', 'quote': 'Payment 30 days'},
        {'id': 'remove', 'clauseId': 'p4', 'quote': 'Deleted obligation'},
        {'id': 'unknown', 'clauseId': 'absent', 'quote': 'Missing quote'},
    ]}
    diff = store.diff(first['id'], 1, 2, actor=OWNER, report=report)
    assert diff == store.diff(first['id'], 1, 2, actor=OWNER, report=report)
    assert [change['type'] for change in diff['changes']] == ['modified', 'deleted', 'added']
    assert diff['changes'][0]['before']['version'] == 1
    assert diff['changes'][0]['after']['version'] == 2
    assert [risk['status'] for risk in diff['riskImpacts']] == ['still_valid', 'review_required', 'removed', 'review_required']
    assert all(risk['basis'] for risk in diff['riskImpacts'])
    assert '语义' in diff['notice']
    reverse = store.diff(first['id'], 2, 1, actor=OWNER)
    assert reverse['fromVersion'] == 2 and reverse['toVersion'] == 1


def test_ambiguous_duplicate_quote_requires_review(tmp_path):
    store = AssetStore(tmp_path / 'assets.db')
    first = store.create('a.txt', b'a', extracted('Repeat', 'Repeat'), actor=OWNER)
    store.add_version(first['id'], 'b.txt', b'b', extracted('Repeat'), actor=OWNER, expected_revision=1)
    result = store.diff(first['id'], 1, 2, actor=OWNER, report={'risks': [{'id': 'r', 'quote': 'Repeat'}]})
    assert result['riskImpacts'][0]['status'] == 'review_required'
