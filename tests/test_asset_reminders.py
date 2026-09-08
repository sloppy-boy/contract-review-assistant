from datetime import UTC, datetime
from app.security import ApiKeyAuthenticator, Principal
from app.asset_store import AssetStore
from app.asset_reminders import dispatch_workspace_reminders


def test_background_reminders_reach_configured_members_without_opening_ui(tmp_path):
    store = AssetStore(tmp_path / 'assets.db')
    owner = Principal('owner', 'a', 'requester')
    asset = store.create('a.txt', b'synthetic', {'text': 'synthetic', 'segments': [{'id': '1', 'location': {'paragraph': 1}, 'text': 'synthetic'}], 'method': 'text'}, actor=owner, shared_with=['reader'])
    store.add_obligation(asset['id'], actor=owner, expected_revision=1, title='renewal', due_at=datetime(2000, 1, 1, tzinfo=UTC), kind='renewal')
    auth = ApiKeyAuthenticator(environment='production', keys={
        'test-owner': {'subject': 'owner', 'tenantId': 'a', 'role': 'requester'},
        'test-reader': {'subject': 'reader', 'tenantId': 'a', 'role': 'reader'},
        'test-other': {'subject': 'other', 'tenantId': 'b', 'role': 'admin'},
    })
    dispatch_workspace_reminders(store, auth)
    dispatch_workspace_reminders(store, auth)
    assert len(store.notifications(actor=owner)) == 1
    assert len(store.notifications(actor=Principal('reader', 'a', 'reader'))) == 1
    assert store.notifications(actor=Principal('other', 'b', 'admin')) == []
