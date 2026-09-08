# Productionization and External Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the locally complete contract review workflow to a production-ready deployment with durable infrastructure, enterprise identity and key management, real retrieval/OCR providers, vendor integrations, and operational acceptance evidence.

**Architecture:** Preserve the existing domain services and adapter seams. Replace local SQLite/in-process implementations behind explicit repository, object-store, queue, identity, and integration interfaces; keep domain payloads reference-only and retain immutable Playbook/review snapshots. Roll out in stages behind migration flags so the local adapters remain available for rollback.

**Tech Stack:** FastAPI, Pydantic, PostgreSQL, S3-compatible object storage, a managed queue (or Redis Streams), OIDC/SAML, SCIM, KMS, OpenTelemetry, Prometheus-compatible metrics, the existing Vue frontend, and the existing pytest/Node test suites.

**Spec:** `docs/UPGRADE_ROADMAP.md`, `docs/PLATFORM_GOVERNANCE.md`, `docs/DEPLOYMENT.md`

## Global Constraints

- Never commit `.env`, `settings.json`, API keys, KMS secrets, runtime databases, uploaded contracts, or model output.
- Task payloads contain identifiers only; contract text is stored in encrypted object storage and fetched by an authorized worker.
- A published Playbook and a review run snapshot are immutable and tenant-scoped.
- Every external delivery is HTTPS-only, allowlisted, signed, idempotent, retried with bounded backoff, and recorded without secrets or contract text.
- Every migration has an expand/verify/contract sequence and a tested rollback or restore procedure.
- Production acceptance requires tenant isolation, backup restore, failure recovery, latency/error SLOs, and human legal review.

---

### Task 1: Freeze the deployment contract and environment inventory

**Files:**
- Create: `docs/PRODUCTION_CONTRACT.md`
- Modify: `docs/DEPLOYMENT.md`, `.env.example`, `docker-compose.yml`
- Test: `tests/test_runtime_config.py`

**Interfaces:**
- Produces the required environment variables and readiness checks consumed by Tasks 2–7.

- [ ] **Step 1: Write the failing configuration tests**

```python
def test_production_config_requires_external_services(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="DATABASE_URL"):
        load_runtime_config()
```

- [ ] **Step 2: Run `python -m pytest tests/test_runtime_config.py -q` and verify the missing-config failure.**
- [ ] **Step 3: Implement `load_runtime_config()` with explicit development/local and production profiles.**
- [ ] **Step 4: Document owners, secret sources, rotation intervals, data classification, RPO/RTO, and rollout flags in `docs/PRODUCTION_CONTRACT.md`.**
- [ ] **Step 5: Run the test and `python -m compileall -q app tests`.**

Required inputs from the operator: deployment host/namespace, expected RPO/RTO, approved regions, domain name, and whether the first rollout is single-region or multi-region.

### Task 2: Replace local persistence with PostgreSQL, object storage, and a durable queue

**Files:**
- Create: `app/repositories.py`, `app/object_store.py`, `app/queue_backend.py`
- Modify: `app/review_runs.py`, `app/task_queue.py`, `app/asset_store.py`, `app/data_lifecycle.py`, `app/api.py`, `Dockerfile`, `requirements.txt`
- Create: `migrations/001_initial.sql`, `migrations/002_indexes.sql`
- Test: `tests/test_repository_contract.py`, `tests/test_object_store_contract.py`, `tests/test_queue_backend_contract.py`, `tests/test_migrations.py`

**Interfaces:**
- `ReviewRepository.create/get/update_progress/complete/cancel/purge`.
- `ObjectStore.put/get/delete/presign` with tenant and content-hash parameters.
- `QueueBackend.enqueue/claim/heartbeat/ack/fail/cancel/requeue_stale`.
- Existing SQLite classes implement these contracts for local tests; PostgreSQL/S3 adapters implement them for production.

- [ ] **Step 1: Add contract tests against the current SQLite adapters.**
- [ ] **Step 2: Run the contract tests and record the expected local behavior.**
- [ ] **Step 3: Add PostgreSQL migrations with tenant-scoped indexes and uniqueness constraints.**
- [ ] **Step 4: Implement the PostgreSQL repository using parameterized SQL and transaction-scoped CAS updates.**
- [ ] **Step 5: Implement encrypted object storage for asset originals and review inputs; store only object keys and SHA-256 hashes in the database.**
- [ ] **Step 6: Implement queue claim/lease semantics on the selected managed queue and keep dead-letter metadata in PostgreSQL.**
- [ ] **Step 7: Add dual-read/dual-write migration flags, a backfill command, and a restore verification command.**
- [ ] **Step 8: Run local contract tests plus a disposable PostgreSQL/S3 integration suite.**

Required inputs: PostgreSQL connection/SSL policy, object-store bucket and retention policy, queue vendor, network allowlist, and migration window.

### Task 3: Enterprise identity, authorization, encryption, and privacy controls

**Files:**
- Create: `app/identity.py`, `app/authorization.py`, `app/kms.py`, `app/pii.py`
- Modify: `app/security.py`, `app/api.py`, `app/asset_api.py`, `app/collaboration_api.py`, `app/workflow_automation.py`
- Test: `tests/test_identity.py`, `tests/test_authorization_matrix.py`, `tests/test_kms_envelope.py`, `tests/test_pii_redaction.py`

**Interfaces:**
- `IdentityProvider.authenticate(token) -> Principal`.
- `Authorization.can(principal, action, resource) -> bool`.
- `KeyProvider.encrypt/decrypt/rotate`.
- `redact_sensitive(value, policy) -> value`.

- [ ] **Step 1: Define a role/action/resource matrix for requester, reviewer, approver, admin, and governance operations.**
- [ ] **Step 2: Write failing tests for cross-tenant reads, delegated approvals, expired sessions, and denied exports.**
- [ ] **Step 3: Implement OIDC JWT verification and SAML assertion mapping behind `IdentityProvider`.**
- [ ] **Step 4: Implement SCIM provisioning/deprovisioning with tenant membership revocation.**
- [ ] **Step 5: Implement KMS envelope encryption for object keys and integration secrets; add key rotation without plaintext export.**
- [ ] **Step 6: Apply PII redaction to logs, traces, webhook payloads, exports, and error responses.**
- [ ] **Step 7: Run authorization matrix, rotation, and redaction tests; perform an external penetration test.**

Required inputs: IdP metadata/client IDs, signing certificates, SCIM endpoint/token, KMS key policy, approved PII fields, and security-review owner.

### Task 4: Production OCR, embeddings, hybrid retrieval, and evaluation gates

**Files:**
- Create: `app/ocr_provider.py`, `app/embedding_provider.py`, `app/reranker.py`
- Modify: `app/document_import.py`, `app/asset_semantic.py`, `app/legal/retrieval.py`, `app/evaluation_gate.py`
- Test: `tests/test_provider_contracts.py`, `tests/test_retrieval_regression.py`
- Data: `eval/dataset/` (synthetic and approved redacted samples only)

**Interfaces:**
- `OcrProvider.extract(bytes, mime_type) -> ExtractedDocument`.
- `EmbeddingProvider.encode(list[str]) -> list[list[float]]`.
- `Reranker.rank(query, candidates) -> list[Candidate]`.

- [ ] **Step 1: Add provider contract tests with deterministic fakes and explicit timeout/error behavior.**
- [ ] **Step 2: Run them locally without network access and verify failure on malformed output.**
- [ ] **Step 3: Implement provider adapters with model checksum validation, bounded input size, no implicit model downloads, and tenant-level quotas.**
- [ ] **Step 4: Wire dense + keyword + rerank retrieval and preserve citation IDs and source locations.**
- [ ] **Step 5: Run held-out Recall@K, MRR, citation accuracy, latency, and cost gates; fail CI on regression.**
- [ ] **Step 6: Validate OCR on approved scanned and mixed PDFs, recording confidence and human-review flags.**

Required inputs: approved OCR/vector/reranker provider, model artifacts and checksums, data residency policy, per-document cost/latency limits, and a redacted evaluation set.

### Task 5: Complete concrete enterprise platform integrations

**Files:**
- Create: `app/integrations/enterprise_im.py`, `app/integrations/ticket.py`, `app/integrations/procurement_crm.py`, `app/integrations/e_signature.py`
- Modify: `app/workflow_automation.py`, `app/collaboration_api.py`, `app/api.py`, `frontend/src/views/SettingsView.vue`
- Test: `tests/test_vendor_contracts.py`, `tests/test_integration_replay.py`

**Interfaces:**
- Each adapter implements `send(event, idempotency_key) -> DeliveryResult` and declares its redacted event schema.
- Vendor-specific failures map to retryable, permanent, and authentication categories.

- [ ] **Step 1: Collect vendor API schemas, sandbox endpoints, signing rules, rate limits, and test tenants.**
- [ ] **Step 2: Write contract tests from those schemas using recorded, secret-free fixtures.**
- [ ] **Step 3: Implement field mapping from the data-minimized collaboration event; reject unsupported fields.**
- [ ] **Step 4: Add replay of failed deliveries with audit authorization and idempotency preservation.**
- [ ] **Step 5: Validate retries, duplicate suppression, signature verification, rate limiting, and alert routing in each vendor sandbox.**

Required inputs: exact vendor products/versions, sandbox tenants, API documentation, credentials in a secret manager, and business owners for message templates.

### Task 6: Production observability, cost attribution, alerts, and failure drills

**Files:**
- Create: `app/telemetry.py`, `app/alerts.py`, `scripts/failure_drill.py`, `scripts/load_test.py`
- Modify: `app/observability.py`, `app/api.py`, `docker-compose.yml`, `.github/workflows/ci.yml`
- Test: `tests/test_observability_contract.py`, `tests/test_failure_recovery.py`

**Interfaces:**
- Metrics: `review_latency_ms`, `review_errors_total`, `llm_tokens_total`, `integration_delivery_failures_total`, `queue_age_seconds`.
- Alert rules use tenant-safe labels and never include contract text or secrets.

- [ ] **Step 1: Write tests for trace/span correlation, cost attribution, redacted attributes, and SLO calculations.**
- [ ] **Step 2: Implement OpenTelemetry traces and Prometheus metrics around upload, queue, retrieval, model calls, export, and integrations.**
- [ ] **Step 3: Add dashboards and alerts for p95 latency, error rate, queue age, provider balance, failed integrations, and storage capacity.**
- [ ] **Step 4: Add load tests for concurrent tenants and a failure drill covering worker loss, provider timeout, database failover, and restore.**
- [ ] **Step 5: Run the drills in staging and attach evidence to the release checklist.**

Required inputs: telemetry backend, alert channel, SLO targets, cost budget, expected concurrency, and staging failure-injection permissions.

### Task 7: Decide and implement native Word editing if required

**Files:**
- Create: `office-addin/` or `app/export_track_changes.py`
- Modify: `app/export_word.py`, `frontend/src/views/ReportDetail.vue`, `docs/DEPLOYMENT.md`
- Test: `tests/test_track_changes.py`, `office-addin/tests/`

- [ ] **Step 1: Confirm whether the deliverable is a report package or edits to the original DOCX.**
- [ ] **Step 2: If native edits are required, define WordprocessingML revision semantics and preserve original package bytes.**
- [ ] **Step 3: Write fixture-based tests for insertions, deletions, comments, tables, numbering, and unsupported constructs.**
- [ ] **Step 4: Implement and validate in supported Word versions; otherwise formally close this task as out of scope and retain the current审查包 export.**

Required input: legal/business decision on native Track Changes, supported Office versions, and a redacted DOCX fixture set.

### Task 8: Staging acceptance, security review, and controlled rollout

**Files:**
- Create: `docs/RELEASE_CHECKLIST.md`, `docs/ROLLBACK_RUNBOOK.md`
- Modify: `docs/DEPLOYMENT.md`, `docs/TESTING.md`, `.github/workflows/ci.yml`
- Test: staging-only integration and smoke suites

- [ ] **Step 1: Run schema migrations against a restored staging snapshot and verify tenant counts/hashes.**
- [ ] **Step 2: Run the full backend/frontend suite, provider contract suite, load test, failure drills, backup restore, and authorization matrix.**
- [ ] **Step 3: Complete security, privacy, legal-rule, and model-output reviews with named approvers.**
- [ ] **Step 4: Deploy a canary tenant, monitor SLOs and delivery alerts, and compare reports against the local baseline.**
- [ ] **Step 5: Expand rollout only after the canary exit criteria pass; retain the SQLite/local path until rollback evidence is recorded.**

---

## Completion criteria

- Production adapters pass the same repository, object-store, queue, identity, and integration contracts as local adapters.
- No secret, contract body, or runtime database appears in Git, logs, traces, webhook payloads, or test fixtures.
- Backup restore meets the declared RPO/RTO; worker/provider/database failure drills recover without duplicate or cross-tenant writes.
- Retrieval/OCR quality and latency meet the approved gates on a held-out, redacted set.
- Named security, infrastructure, model, and legal owners approve the release checklist.
