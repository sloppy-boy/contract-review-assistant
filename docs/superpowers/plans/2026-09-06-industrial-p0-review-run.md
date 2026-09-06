# Industrial P0 Review Run Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the in-memory contract-review task into a durable, traceable review run with legal-corpus snapshots and human review decisions.

**Architecture:** Keep LangGraph state in-process for one pipeline execution, but persist its externally relevant lifecycle in SQLite. A `ReviewRunStore` module is the seam for task state, stage events, evidence snapshots, and human dispositions; FastAPI routes and Vue consume that small interface. SQLite is the default adapter and retains a migration path to Postgres without changing callers.

**Tech Stack:** Python 3.11, FastAPI, SQLite stdlib, Pydantic, Vue 3, Element Plus, pytest.

**Spec:** `PROJECT_SPEC.md`, plus the industrial flagship roadmap agreed in this conversation.

## Global Constraints

- Preserve the existing LangGraph blackboard as per-run state; do not share it across requests.
- Persist only structured report data, legal snapshots, lifecycle events, and human decisions; raw contract text is not stored in the first P0 slice.
- Every report evidence record contains legal corpus version, source URL, content hash, and retrieval timestamp.
- High-risk findings and findings without direct legal basis start in `pending_review`.
- All new behavior is test-first; existing offline demo reports remain usable.

---

### Task 1: Durable review-run store

**Files:**
- Create: `app/review_runs.py`
- Modify: `app/config.py`
- Modify: `tests/test_core.py`

**Interfaces:**
- Consumes: `pathlib.Path`, JSON-serializable reports.
- Produces: `ReviewRunStore(path)` with `create_run`, `update_progress`, `complete_run`, `fail_run`, `get_run`, and `list_events`.

- [ ] **Step 1: Write failing tests**

```python
def test_review_run_survives_store_reopen(tmp_path):
    store = ReviewRunStore(tmp_path / "review_runs.db")
    run = store.create_run(contract_type="purchase")
    store.update_progress(run["id"], stage=1, status="running", detail="13 workers")
    reopened = ReviewRunStore(tmp_path / "review_runs.db")
    assert reopened.get_run(run["id"])["stageDetail"] == "13 workers"

def test_review_run_records_ordered_events(tmp_path):
    store = ReviewRunStore(tmp_path / "review_runs.db")
    run = store.create_run(contract_type="sale")
    store.update_progress(run["id"], stage=0, status="running")
    assert [e["type"] for e in store.list_events(run["id"])] == ["created", "progress"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_core.py -k review_run -v`

Expected: FAIL because `app.review_runs` does not exist.

- [ ] **Step 3: Write minimal implementation**

Create tables `review_runs` and `review_run_events`. Store UTC ISO timestamps, status, stage, stage detail, stage times, report JSON, error, and version metadata. The store must create its schema idempotently on initialization and return JSON-safe dictionaries.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_core.py -k review_run -v`

Expected: PASS.

### Task 2: Replace the in-memory task table

**Files:**
- Modify: `app/api.py`
- Modify: `app/config.py`
- Modify: `tests/test_core.py`

**Interfaces:**
- Consumes: `ReviewRunStore` from Task 1 and `run_pipeline`.
- Produces: `POST /upload`, `GET /report/{task_id}`, and `GET /review-runs/{task_id}/events` backed by persisted runs.

- [ ] **Step 1: Write failing tests**

```python
def test_report_endpoint_reads_a_persisted_run(monkeypatch, tmp_path):
    monkeypatch.setattr(api_mod, "review_run_store", ReviewRunStore(tmp_path / "runs.db"))
    run = api_mod.review_run_store.create_run(contract_type="purchase")
    api_mod.review_run_store.fail_run(run["id"], "provider unavailable")
    response = TestClient(api_mod.app).get(f"/report/{run['id']}")
    assert response.json()["error"] == "provider unavailable"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_core.py -k persisted_run -v`

Expected: FAIL because the endpoint reads `TASKS`.

- [ ] **Step 3: Write minimal implementation**

Remove `TASKS`, `_tasks_lock`, trimming logic, and all task-dictionary replacement. Create the run before starting the thread; send progress through `ReviewRunStore`; complete or fail the same run. Add a read-only event route returning events after an optional event id.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_core.py -k persisted_run -v`

Expected: PASS.

### Task 3: Legal-corpus snapshots and report evidence

**Files:**
- Create: `app/legal/corpus_snapshot.py`
- Modify: `app/legal/manual.py`
- Modify: `app/nodes/report.py`
- Modify: `tests/test_core.py`

**Interfaces:**
- Consumes: verified legal manual entries.
- Produces: `LegalCorpusSnapshot.create(manual_entries)` and report-level `meta.legalCorpus` plus finding-level immutable evidence metadata.

- [ ] **Step 1: Write failing tests**

```python
def test_snapshot_hash_changes_when_legal_text_changes():
    a = LegalCorpusSnapshot.create([{"articleId": "CIVIL-585", "text": "A", "sourceUrl": "https://example.test/a"}])
    b = LegalCorpusSnapshot.create([{"articleId": "CIVIL-585", "text": "B", "sourceUrl": "https://example.test/a"}])
    assert a.contentHash != b.contentHash

def test_report_carries_legal_snapshot():
    report = report_node(minimal_state_with_one_finding())["report"]
    assert report["meta"]["legalCorpus"]["contentHash"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_core.py -k "snapshot or legal_snapshot" -v`

Expected: FAIL because reports have no corpus snapshot.

- [ ] **Step 3: Write minimal implementation**

Build a deterministic SHA-256 hash from normalized verified legal records. Include `version`, `generatedAt`, `contentHash`, and per-article source metadata. Do not mutate existing `legalBasis`; add snapshot data at report metadata level.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_core.py -k "snapshot or legal_snapshot" -v`

Expected: PASS.

### Task 4: Human review state machine

**Files:**
- Modify: `app/review_runs.py`
- Modify: `app/api.py`
- Modify: `app/nodes/report.py`
- Modify: `tests/test_core.py`

**Interfaces:**
- Consumes: a completed review run and a finding id.
- Produces: `set_disposition(run_id, finding_id, decision, reason)` and `POST /review-runs/{run_id}/findings/{finding_id}/disposition`.

- [ ] **Step 1: Write failing tests**

```python
def test_high_risk_finding_defaults_to_pending_review():
    report = report_node(minimal_state_with_high_risk())["report"]
    assert report["risks"][0]["reviewStatus"] == "pending_review"

def test_disposition_requires_reason_for_rejection(tmp_path):
    store = ReviewRunStore(tmp_path / "runs.db")
    run = store.create_run(contract_type="purchase")
    with pytest.raises(ValueError):
        store.set_disposition(run["id"], "f-1", "rejected", "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_core.py -k disposition -v`

Expected: FAIL because review status and dispositions do not exist.

- [ ] **Step 3: Write minimal implementation**

Add `pending_review`, `accepted`, `accepted_with_changes`, `rejected`, and `escalated` statuses. Require a reason for rejected, changed, and escalated decisions. Store decision events separately from the LLM finding; return the decision in report reads without mutating immutable model evidence.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_core.py -k disposition -v`

Expected: PASS.

### Task 5: Legal-review workbench surface

**Files:**
- Modify: `frontend/src/api.js`
- Modify: `frontend/src/views/Workbench.vue`
- Modify: `frontend/src/views/ReportDetail.vue`
- Modify: `frontend/src/App.vue`
- Test: `frontend/tests/ReportDetail.test.tsx` or a Vue-native replacement

**Interfaces:**
- Consumes: persisted run lifecycle, event list, report `meta.legalCorpus`, and finding `reviewStatus`.
- Produces: reconnectable review-run progress, visible corpus version, and disposition controls for report findings.

- [ ] **Step 1: Write failing frontend tests**

```javascript
it('shows the legal corpus version for a completed report', async () => {
  render(ReportDetail, { props: { report: reportWithLegalCorpus } })
  expect(await screen.findByText(/法条快照/)).toBeTruthy()
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- ReportDetail`

Expected: FAIL because the report has no legal-corpus panel.

- [ ] **Step 3: Write minimal implementation**

Restore an existing run from `localStorage` on page load, show persisted stage state, and render the legal snapshot plus a clear human-review action for each risk. Do not claim a finding is final until a disposition exists.

- [ ] **Step 4: Run frontend tests and production build**

Run: `npm test -- ReportDetail && npm run build`

Expected: PASS and production bundle builds.

### Task 6: Documentation and regression gate

**Files:**
- Modify: `README.md`
- Modify: `PROJECT_SPEC.md`
- Create: `docs/operations/review-run-recovery.md`

**Interfaces:**
- Consumes: completed P0 behavior.
- Produces: deployment, recovery, retention, and legal-evidence explanation that matches code.

- [ ] **Step 1: Document operational behavior**

Describe SQLite default storage location, restart behavior for queued/running runs, no-raw-contract persistence rule, legal corpus hash meaning, and human disposition audit trail.

- [ ] **Step 2: Run full regression**

Run: `python -m pytest tests/ -q && python scripts/check_report_schema.py && npm run build`

Expected: all checks pass.
