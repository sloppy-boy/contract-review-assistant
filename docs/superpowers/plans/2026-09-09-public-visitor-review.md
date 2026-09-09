# Public Visitor Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an unauthenticated browser obtain an isolated anonymous session and complete the public contract-review flow without seeing workspace-key prompts or unrelated authentication errors.

**Architecture:** A focused `VisitorSessionManager` signs and verifies opaque HMAC visitor tokens and a focused rate limiter protects anonymous uploads. FastAPI resolves either an existing API key or a visitor bearer token into the same `Principal` type; endpoint role checks retain administrative boundaries. The Lash frontend bootstraps a visitor token in its shared API client and lazily mounts views.

**Tech Stack:** Python 3.11, FastAPI, HMAC-SHA256, Vue 3, Element Plus, Node test runner, pytest.

**Spec:** `docs/superpowers/specs/2026-09-09-public-visitor-review-design.md`

## Global Constraints

- Production administrator credentials remain server-side and keep protecting administrative capabilities.
- Visitor history and reports are isolated by a random visitor tenant identifier.
- Default visitor upload allowance is three submissions per client IP per hour.
- No API credentials, visitor signing secret, uploaded contracts, review databases, or runtime logs are committed.

---

### Task 1: Signed visitor sessions

**Files:**
- Create: `app/visitor_sessions.py`
- Create: `tests/test_visitor_sessions.py`
- Modify: `.env.example`

**Interfaces:**
- Produces: `VisitorSessionManager(secret: str, ttl_seconds: int)`, `issue() -> tuple[str, Principal]`, and `authenticate(token: str) -> Principal`.

- [ ] Write tests proving a token round-trips, two tokens receive different tenants, tampering fails, and expiry fails.
- [ ] Run `python -m pytest tests/test_visitor_sessions.py -q` and verify the missing module or class makes it fail.
- [ ] Implement URL-safe payload encoding, HMAC-SHA256 signing with constant-time comparison, and expiry validation.
- [ ] Add documented `CRA_VISITOR_SIGNING_SECRET` and `CRA_VISITOR_TOKEN_TTL_SECONDS` environment variables without real values.
- [ ] Run the focused tests and commit the green change.

### Task 2: API authentication and visitor authorization

**Files:**
- Modify: `app/api.py`
- Create: `tests/test_visitor_api.py`

**Interfaces:**
- Produces: `POST /visitor/session`, dual-credential `current_principal`, and visitor-compatible public review endpoints.
- Consumes: `VisitorSessionManager.authenticate()` from Task 1.

- [ ] Write integration tests showing no credential returns 401, a new visitor session can list its empty history, visitor A cannot read visitor B's run, and visitors cannot access `/settings`.
- [ ] Run `python -m pytest tests/test_visitor_api.py -q` and verify session creation currently returns 404.
- [ ] Add bearer-token parsing and the visitor-session endpoint; keep `X-API-Key` precedence.
- [ ] Add explicit admin-role checks to settings, provider, platform, collaboration, asset, and Playbook management endpoints touched by visitor access.
- [ ] Run visitor API tests plus existing security, Playbook, collaboration, asset, and core API tests; commit when green.

### Task 3: Anonymous upload rate limit

**Files:**
- Create: `app/visitor_rate_limit.py`
- Modify: `app/api.py`
- Modify: `tests/test_visitor_api.py`

**Interfaces:**
- Produces: `SlidingWindowRateLimiter.allow(key: str, now: float | None = None) -> tuple[bool, int]`.

- [ ] Add tests showing the fourth visitor upload from one IP receives 429, a different IP is independent, and API-key admins bypass the visitor limit.
- [ ] Run the focused test and verify it fails because no limit exists.
- [ ] Implement a lock-protected in-memory sliding window and enforce it immediately before visitor run creation.
- [ ] Run the focused and API suites; commit when green.

### Task 4: Frontend visitor bootstrap and retry

**Files:**
- Modify: `frontend/src/api.js`
- Modify: `frontend/src/views/Workbench.vue`
- Create: `frontend/tests/visitor-auth.test.js`

**Interfaces:**
- Produces: `ensureVisitorSession()`, authenticated `apiFetch()`, and a single automatic retry after visitor-token 401.

- [ ] Write Node tests proving the first protected request creates a visitor session, sends `Authorization: Bearer`, retries once after 401, and never sends an administrator key unless one was explicitly saved.
- [ ] Run `npm test -- visitor-auth.test.js` from `frontend` and verify the absent bootstrap behavior fails.
- [ ] Implement deduplicated session creation and token persistence, then remove the workspace-key field from the public Workbench.
- [ ] Run focused frontend tests and commit when green.

### Task 5: Lazy views and visitor navigation

**Files:**
- Modify: `frontend/src/App.vue`
- Modify: `frontend/src/views/HistoryView.vue`
- Create: `frontend/tests/app-mounting.test.js`

**Interfaces:**
- Consumes: visitor-aware `apiFetch()` from Task 4.

- [ ] Write tests proving initial render mounts only Workbench, visitor navigation omits administrative tabs, and history maps 401/429 to specific messages.
- [ ] Run the focused test and verify current `v-show` mounting or tab visibility fails.
- [ ] Replace side-effecting `v-show` views with selected `v-if` mounts and derive public tabs from the resolved principal role.
- [ ] Run focused tests and the full frontend suite; commit when green.

### Task 6: Full verification and deployment

**Files:**
- Modify: `README.md` only if visitor deployment variables are not already documented adequately.
- Modify: `/opt/contract-review-assistant/.env` on the server without printing the generated secret.

**Interfaces:**
- Validates all interfaces produced by Tasks 1–5.

- [ ] Run `python -m pytest tests -q` and the full frontend test/build commands.
- [ ] Generate the visitor signing secret on the server, pull the reviewed commit, and recreate the backend without deleting its data volume.
- [ ] Deploy the frontend to EdgeOne from the same Git revision.
- [ ] Open the public frontend in a clean Chrome site state and verify visitor session creation, upload, progress, report, refresh, and isolated history.
- [ ] Confirm the repository is clean and no secret or runtime data appears in Git history or the working tree.
