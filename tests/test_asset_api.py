"""Synthetic asset workflows: authentication, ACLs, import and reminders."""
import pytest
from fastapi.testclient import TestClient
from app import api
from app.security import ApiKeyAuthenticator


@pytest.fixture
def client(monkeypatch, tmp_path):
    from app.asset_store import AssetStore
    from app.data_lifecycle import DataLifecycleStore
    monkeypatch.setattr(api, "asset_store", AssetStore(tmp_path / "assets.db"), raising=False)
    monkeypatch.setattr(api, "lifecycle_store", DataLifecycleStore(tmp_path / "lifecycle.db"), raising=False)
    monkeypatch.setattr(api, "authenticator", ApiKeyAuthenticator(environment="production", keys={
        "test-owner": {"subject": "owner", "tenantId": "a", "role": "requester"},
        "test-reader": {"subject": "reader", "tenantId": "a", "role": "reader"},
        "test-peer": {"subject": "peer", "tenantId": "a", "role": "legal_reviewer"},
        "test-admin": {"subject": "admin", "tenantId": "a", "role": "admin"},
        "test-other": {"subject": "other", "tenantId": "b", "role": "admin"},
    }))
    result = TestClient(api.app)
    result.headers["X-API-Key"] = "test-owner"
    return result


def imported(client):
    response = client.post("/contract-assets/import", files=[("files", ("sample.txt", "采购合同\n甲方：合成企业\n付款期限为三十日。".encode(), "text/plain"))])
    assert response.status_code == 200, response.text
    return response.json()["results"][0]["asset"]


def test_import_batch_reports_partial_failure_without_creating_empty_assets(client):
    response = client.post("/contract-assets/import", files=[
        ("files", ("a.txt", "合成合同\n付款期限三十日".encode(), "text/plain")),
        ("files", ("bad.docx", b"invalid", "application/octet-stream")),
    ])
    assert response.status_code == 200
    results = response.json()["results"]
    assert results[0]["asset"]["visibility"] == "private"
    assert results[1]["error"]
    assert len(client.get("/contract-assets").json()["assets"]) == 1
    asset = results[0]["asset"]
    original = client.get(f"/contract-assets/{asset['id']}/original")
    assert original.content == "合成合同\n付款期限三十日".encode()


def test_private_assets_are_hidden_from_all_read_paths_and_can_be_shared(client):
    asset = imported(client)
    client.headers["X-API-Key"] = "test-reader"
    for path in [f"/contract-assets/{asset['id']}", f"/contract-assets/{asset['id']}/original", f"/contract-assets/{asset['id']}/obligations"]:
        assert client.get(path).status_code == 404
    assert client.get("/contract-assets").json()["assets"] == []
    assert client.get("/contract-assets/search", params={"q": "付款"}).json()["citations"] == []
    assert client.post("/contract-assets/ask", json={"question": "付款期限"}).json()["citations"] == []
    client.headers["X-API-Key"] = "test-owner"
    response = client.patch(f"/contract-assets/{asset['id']}", json={"expectedRevision": asset["revision"], "title": "已共享合同", "tags": ["采购"], "visibility": "private", "sharedWith": ["reader"]})
    assert response.status_code == 200, response.text
    client.headers["X-API-Key"] = "test-reader"
    assert client.get(f"/contract-assets/{asset['id']}").status_code == 200
    assert client.get("/contract-assets/search", params={"q": "付款"}).json()["citations"][0]["assetId"] == asset["id"]
    assert client.post("/contract-assets/ask", json={"question": "付款期限"}).json()["citations"]
    client.headers["X-API-Key"] = "test-other"
    assert client.get(f"/contract-assets/{asset['id']}").status_code == 404


def test_revisions_and_member_validation_are_atomic(client):
    asset = imported(client)
    payload = {"expectedRevision": asset["revision"], "title": "new", "tags": [], "visibility": "private", "sharedWith": ["other"]}
    assert client.patch(f"/contract-assets/{asset['id']}", json=payload).status_code == 422
    payload["sharedWith"] = []
    assert client.patch(f"/contract-assets/{asset['id']}", json=payload).status_code == 200
    assert client.patch(f"/contract-assets/{asset['id']}", json=payload).status_code == 409


def test_obligation_reminders_are_idempotent_and_scoped(client):
    asset = imported(client)
    response = client.post(f"/contract-assets/{asset['id']}/obligations", json={"expectedRevision": asset["revision"], "title": "合成续签", "kind": "renewal", "dueAt": "2020-01-01T00:00:00Z"})
    assert response.status_code == 200, response.text
    asset = response.json()
    obligations = client.get(f"/contract-assets/{asset['id']}/obligations").json()["obligations"]
    assert len(obligations) == 1
    client.post("/contract-assets/reminders/check")
    client.post("/contract-assets/reminders/check")
    notifications = client.get("/contract-assets/notifications").json()["notifications"]
    assert len(notifications) == 1
    read = client.post(f"/contract-assets/notifications/{notifications[0]['id']}/read")
    assert read.status_code == 200
    completed = client.post(f"/contract-assets/{asset['id']}/obligations/{obligations[0]['id']}/complete", json={"expectedRevision": asset["revision"]})
    assert completed.status_code == 200
    client.headers["X-API-Key"] = "test-other"
    assert client.get("/contract-assets/notifications").json()["notifications"] == []
    assert client.post(f"/contract-assets/notifications/{notifications[0]['id']}/read").status_code == 404


def test_reader_import_and_anonymous_reads_denied(client):
    client.headers["X-API-Key"] = "test-reader"
    assert client.post("/contract-assets/import", files={"files": ("a.txt", b"synthetic")}).status_code == 403
    client.headers.pop("X-API-Key")
    assert client.get("/contract-assets").status_code == 401


def test_empty_question_and_excessive_batch_rejected(client):
    assert client.post("/contract-assets/ask", json={"question": "   "}).status_code == 422
    assert client.post("/contract-assets/import", files=[("files", (f"{i}.txt", b"synthetic")) for i in range(11)]).status_code == 422


def test_naive_obligation_datetime_rejected_without_mutation(client):
    asset = imported(client)
    response = client.post(f"/contract-assets/{asset['id']}/obligations", json={"expectedRevision": asset["revision"], "title": "付款", "kind": "obligation", "dueAt": "2030-01-01"})
    assert response.status_code in {409, 422}
    assert client.get(f"/contract-assets/{asset['id']}/obligations").json()["obligations"] == []


def test_frontend_bundle_is_not_intercepted_by_contract_routes(client, tmp_path, monkeypatch):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<div id='app'></div>", encoding="utf-8")
    (tmp_path / "assets" / "synthetic.js").write_text("window.synthetic = true", encoding="utf-8")
    monkeypatch.setattr(api, "FRONTEND_DIST", tmp_path)
    response = client.get("/assets/synthetic.js")
    assert response.status_code == 200
    assert "window.synthetic" in response.text


def test_version_upload_download_diff_and_acl(client):
    asset = imported(client)
    root = f"/contract-assets/{asset['id']}"
    response = client.post(root + '/versions', data={'expectedRevision': asset['revision']}, files={'file': ('revision.txt', '采购合同\n甲方：合成企业\n付款期限为六十日。'.encode())})
    assert response.status_code == 200, response.text
    assert response.json()['version'] == 2
    assert len(client.get(root + '/versions').json()['versions']) == 2
    assert '三十日' in client.get(root + '/versions/1').json()['text']
    assert '三十日' in client.get(root + '/versions/1/original').content.decode()
    assert client.get(root + '/diff', params={'from': 1, 'to': 2}).json()['changes'][0]['type'] == 'modified'
    assert client.post(root + '/versions', data={'expectedRevision': 1}, files={'file': ('stale.txt', b'stale')}).status_code == 409
    for key in ['test-reader', 'test-other']:
        client.headers['X-API-Key'] = key
        for path in ['/versions', '/versions/1', '/versions/1/original', '/diff?from=1&to=2']:
            assert client.get(root + path).status_code == 404


def test_diff_rejects_foreign_unfinished_or_unbound_report_runs(client, monkeypatch):
    import hashlib
    from types import SimpleNamespace
    asset = imported(client)
    root = f"/contract-assets/{asset['id']}"
    run = {'id': 'run', 'tenantId': 'a', 'status': 'done', 'inputTextHash': hashlib.sha256(asset['text'].encode()).hexdigest(),
           'report': {'risks': [{'id': 'r', 'quote': '付款期限为三十日。'}]}}
    monkeypatch.setattr(api, 'review_run_store', SimpleNamespace(get_run=lambda _: run))
    good = client.get(root + '/diff', params={'from': 1, 'to': 1, 'runId': 'run'})
    assert good.status_code == 200, good.text
    assert good.json()['riskImpacts'][0]['status'] == 'still_valid'
    run['tenantId'] = 'b'
    assert client.get(root + '/diff?from=1&to=1&runId=run').status_code == 404
    run['tenantId'] = 'a'
    run['status'] = 'running'
    assert client.get(root + '/diff?from=1&to=1&runId=run').status_code == 409
    run['status'] = 'done'
    run['inputTextHash'] = 'unrelated'
    assert client.get(root + '/diff?from=1&to=1&runId=run').status_code == 409
    run.pop('inputTextHash')
    assert client.get(root + '/diff?from=1&to=1&runId=run').status_code == 409


def test_legal_hold_blocks_asset_delete_and_admin_can_purge_after_release(client):
    asset = imported(client)
    client.headers["X-API-Key"] = "test-peer"
    hold = client.post("/platform/legal-holds", json={"resourceType": "asset", "resourceId": asset["id"], "reason": "诉讼保全"})
    assert hold.status_code == 200
    client.headers["X-API-Key"] = "test-owner"
    assert client.post(f"/contract-assets/{asset['id']}/delete", json={"reason": "到期清理"}).status_code == 409
    client.headers["X-API-Key"] = "test-peer"
    assert client.delete(f"/platform/legal-holds/asset/{asset['id']}").status_code == 200
    client.headers["X-API-Key"] = "test-owner"
    deleted = client.post(f"/contract-assets/{asset['id']}/delete", json={"reason": "到期清理"})
    assert deleted.status_code == 200 and client.get(f"/contract-assets/{asset['id']}").status_code == 404
    client.headers["X-API-Key"] = "test-admin"
    preview = client.post("/platform/data-lifecycle/purge", json={"before": "2999-01-01T00:00:00+00:00", "dryRun": True})
    assert preview.status_code == 200 and preview.json()["eligible"] == [asset["id"]]
    purged = client.post("/platform/data-lifecycle/purge", json={"before": "2999-01-01T00:00:00+00:00", "dryRun": False})
    assert purged.status_code == 200 and purged.json()["deleted"] == [asset["id"]]
