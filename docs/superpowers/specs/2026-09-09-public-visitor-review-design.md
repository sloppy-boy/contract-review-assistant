# Public visitor review design

## Problem

The production API currently accepts only administrator-managed `X-API-Key` credentials. The public workbench consequently asks every visitor for a workspace key, while hidden views make protected requests during initial page mount. A new visitor cannot upload a contract and sees `authentication required` and history-loading errors after refresh.

## Intended experience

A visitor opens the public site and can immediately upload or paste a contract, wait for the review, read the resulting report, refresh the page, and see only that browser's review history. The visitor never receives or enters an administrator credential.

Administrative capabilities remain protected by configured workspace API keys. These include provider settings, platform operations, Playbook mutation, collaboration administration, contract assets, feedback export, and data-lifecycle operations.

## Authentication model

The backend exposes `POST /visitor/session`. It returns a short-lived signed visitor token containing a random subject, a random tenant identifier, the `visitor` role, issued-at time, and expiry time. The signature uses HMAC-SHA256 with `CRA_VISITOR_SIGNING_SECRET`, which is required in production and is never returned to the browser.

The frontend stores the opaque visitor token for the current site and sends it as `Authorization: Bearer <token>`. Existing `X-API-Key` authentication remains available for administrators. Server authentication accepts either credential and derives a `Principal`; a request carrying neither receives 401.

Tokens expire after seven days by default. An expired or invalid visitor token causes the frontend to request one replacement session and retry the original request once. A workspace API key, when explicitly configured by an administrator, takes precedence over the visitor token.

## Authorization and isolation

Visitor principals may use only the public review surface:

- list active Playbooks needed to select a review policy;
- upload a contract and create a review run;
- poll, cancel, and read their own review runs;
- list their own review history;
- export their own report.

Every review run remains scoped by the principal's tenant identifier. Random visitor tenant IDs prevent one browser from listing or opening another visitor's runs. Existing role checks continue to reject `visitor` for administrative mutation and human-review actions.

The public navigation shows Workbench, Report detail, History, and Evaluation. Administrative navigation is shown only after a valid workspace API key has established an administrative identity. This design does not expose the key in the frontend bundle.

## Abuse controls

Visitor session creation and review upload are rate limited by the client IP. Defaults are configurable through environment variables. The initial deployment permits three review uploads per IP per hour and retains the existing contract-length and pipeline-concurrency limits. API-key principals are not subject to the visitor upload quota.

The limiter returns HTTP 429 with a clear retry message. It is deliberately process-local for this single-instance portfolio deployment. A future multi-instance deployment must move counters to Redis or another shared store.

## Frontend lifecycle

Before the first authenticated API request, the frontend initializes its visitor session. It renders the Workbench while initialization runs and disables the submit button briefly. The workspace-key input is removed from the public Workbench.

Views with API side effects are mounted only when selected. Refreshing the Workbench therefore does not mount History, Settings, or Evaluation and cannot emit unrelated authentication errors.

History maps HTTP 401, 429, and network failures to specific messages instead of the current generic error. Visitor-session recovery happens in the shared API client, so upload, polling, history, and report reads follow the same behavior.

## Tests

Backend tests cover token issuance, signature and expiry rejection, visitor tenant isolation, allowed review endpoints, denied administrative endpoints, and the visitor upload rate limit. Existing API-key tests remain unchanged and must continue to pass.

Frontend tests cover automatic session creation, authenticated retry after an expired visitor token, absence of the workspace-key field, and lazy mounting of protected views. An end-to-end browser check uses a clean site state and verifies open, upload, task progress, report completion, refresh, and isolated history.

## Deployment

Generate `CRA_VISITOR_SIGNING_SECRET` on the server and keep it only in `/opt/contract-review-assistant/.env`. Configure the EdgeOne origin to call the Alibaba Cloud API and retain the existing CORS origin allow-list. Deploy backend first, then frontend, so old administrator-key behavior remains available during the transition.

No API credentials, visitor signing secret, uploaded contracts, review databases, or runtime logs are committed.
