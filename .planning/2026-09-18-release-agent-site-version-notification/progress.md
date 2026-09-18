# Progress: Release Agent Site Version and Notifications

## Session log

### Planning initialization

- Confirmed user decisions `1A 2A 3A 4A 5A`.
- Created OpenSpec proposal, design, delta specs, and tasks for `release-agent-site-version-notification`.
- `openspec validate release-agent-site-version-notification --type change --strict` passed.
- Implementation started after explicit `opsx:apply`; each task below records code/test evidence before its checkbox was updated.

### Task 1.1

- Added the fixed `release_version_sync` HTTP MCP action with Release Agent/admin authorization and manifest-backed input validation.
- Added API tests proving arbitrary agent/URL/version inputs cannot redirect or override the sync.
- Focused evidence: `pytest -q tests/test_release_agent_site_version_notification.py ...` → 23 passed.

### Task 1.2

- Added `release_version_syncs` persistence with a unique `(release_id, transition)` key and idempotent replay behavior.
- Site version is persisted in `site_config.version` and returned by the existing site configuration endpoint.
- Focused evidence: the same release lifecycle test run → 23 passed.

### Task 1.3

- Trusted Runner callback integration now applies version synchronization only for a healthy `succeeded` transition; failed/rolled-back transitions do not overwrite the site setting.
- Callback integration evidence: `pytest -q tests/test_release_agent_site_version_notification.py` → 6 passed.

### Task 2.1

- Added sanitized lifecycle summary rendering for success, failure, rollback, and reconciliation outcomes; secrets and signed payloads are not included.
- Evidence: notification summary tests pass in `pytest -q tests/test_release_agent_site_version_notification.py` → 7 passed.

### Task 2.2

- Added dedicated Release Agent Feishu delivery selection, per-transition deduplication, missing-destination handling, and provider-failure audit without mutating authoritative release state.
- Evidence: delivery deduplication and failure tests pass in the same 7-test focused run.

### Task 2.3

- Channel API now permits an administrator to bind a dedicated Feishu channel to the synthetic `release-agent` identity while preserving ordinary Agent bindings.
- Channels UI exposes the explicit Release Agent option and `release_chat_id`; Site UI exposes the persisted version override used by the release sync.
- Evidence: API/UI regression subset → 14 passed.

### Task 3.1

- Added callback-to-sync and callback-to-notification integration coverage, including duplicate callback/replay deduplication and failed-transition non-overwrite behavior.
- Evidence: `pytest -q apps/api/tests/test_release_agent_site_version_notification.py` → 8 passed.

### Task 3.2

- Python compile validation passed.
- Relevant release, channel, site, and ReAct regression suite: 85 passed, 58 existing deprecation warnings.
- Frontend `npm run build` passed; only existing Rollup pure-comment/chunk-size warnings remain.

### Task 3.3

- Added `docs/release-agent-site-version-notification.md` covering configuration, sanitized audit fields, retry/disable behavior, and safe rollback.
- Documentation and static UI/API checks are complete; Release Agent retains no host/Docker/SSH/ACR authority.

## Verification run — 2026-09-18

- OpenSpec status/instructions: 9/9 tasks complete; state `all_done`.
- `openspec validate release-agent-site-version-notification --type change --strict`: passed.
- `openspec validate --specs --strict`: 25 specs passed, 0 failed.
- Relevant API/regression suite: 85 passed, 58 existing deprecation warnings.
- `python -m compileall -q app`: passed.
- Frontend `npm run build`: passed; only existing Rollup annotation/chunk-size warnings.
- `git diff --check`: passed.
