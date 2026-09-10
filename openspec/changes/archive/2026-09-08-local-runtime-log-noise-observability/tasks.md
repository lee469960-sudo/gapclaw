## 1. Access Log Noise Control

- [x] 1.1 Add a focused API access-log filter for expected `check_status` HTTP 304 responses and verify non-304 `check_status` responses plus unrelated access logs still emit.
- [x] 1.2 Cover the access-log filter with unit tests or logging-config tests that verify expected 304 polling is suppressed while errors and unexpected statuses remain visible.

## 2. Agent Runtime Progress Logging

- [x] 2.1 Replace repeated per-trigger `no_progress_hint` info logs with per-agent streak aggregation that reports agent id, iteration, streak length, and aggregate count or interval; verify progress resets the aggregate window.
- [x] 2.2 Add focused runtime tests for repeated no-progress rounds, progress reset, and operator-visible aggregate output.

## 3. Telegram Poller Observability

- [x] 3.1 Add per-channel Telegram poll failure streak tracking with safe structured fields for component, class, exception type, channel id, retry/backoff state, and repeat count; verify bot tokens are not logged.
- [x] 3.2 Emit compact repeated-failure logs and a recovery log after successful polling resumes; verify first failure and failure-class changes still preserve traceback-level detail.
- [x] 3.3 Add tests for first failure, repeated same-class failure, changed failure class, and recovery after failure.

## 4. LLM Error Classification

- [x] 4.1 Extend LLM retry logging for 429/529 and transport failures with stable key-value fields for component, class, provider, model, status, attempt, max attempts, backoff, and sanitized endpoint category.
- [x] 4.2 Verify 429 is reported as provider throttling/degradation rather than a business-code failure, and verify logs do not include API keys or token-bearing URLs.
- [x] 4.3 Add tests for HTTP 429 retry logging, transport error logging, and final exhausted-retry error classification.

## 5. Diagnostics Consumers

- [x] 5.1 Update `scripts/log-triage.sh` only where the new structured fields improve recent/all-time counters, and verify it still works against existing pre-change plain-text logs.
- [x] 5.2 Update the system-logs MCP stats/search behavior only if needed to expose the new structured classes, and verify existing `api`, `web`, `cloudflared`, and `im_events` sources remain compatible.
- [x] 5.3 Update `docs/ops/local-healthcheck.md` and `docs/system-log-analyst.md` with final structured field names and examples; verify docs match implemented behavior.

## 6. Acceptance

- [x] 6.1 Run targeted backend tests for logging filters, Agent runtime aggregation, Telegram poller streaks, LLM retry classification, and system-logs compatibility.
- [x] 6.2 Run `./scripts/log-triage.sh` against the local log file and verify output distinguishes recent-window counts from all-time totals.
- [x] 6.3 Run `./scripts/healthcheck-local.sh` and verify yellow/red results reflect runtime state rather than script or table-name failures.
- [x] 6.4 Run `openspec validate local-runtime-log-noise-observability --strict` and verify the change is valid before implementation sign-off.
