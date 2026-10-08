# Findings: reliable-scheduled-task-sla

## 2026-10-08 — Planning initialization

- Used `planning-with-files` because the user requested persistent file planning for a multi-step OpenSpec implementation.
- Current OpenSpec change: `openspec/changes/reliable-scheduled-task-sla/`.
- Read required artifacts:
  - `proposal.md`
  - `design.md`
  - `specs/session-scheduled-tasks/spec.md`
  - `tasks.md`
- `openspec/changes/reliable-scheduled-task-sla/tasks.md` is the only formal task source.
- The change modifies existing capability `session-scheduled-tasks`; it does not introduce a new capability.
- The planning files intentionally map tasks to phases and do not redefine the requirements.
- Prior Planning with Files state was stale:
  - `.planning/.active_plan` pointed at `2026-09-28-react-engine-unified-output`.
  - root `task_plan.md`, `findings.md`, and `progress.md` still described `agent-session-capability-router`.
- The new change was created after investigating scheduled-task timing symptoms:
  - current durable scheduler Worker can synchronously execute Agent work after creating due runs;
  - long Agent execution and LLM/MCP retries can delay future scans;
  - Docker scheduled-task Workers are profile/env gated and can be absent while API is running;
  - notification failure is already intended to stay independent from task success, but diagnostics need to make this clear.
- Current spec focus:
  - 15-second run-creation SLA while scheduler Worker is healthy;
  - scheduler/executor/notification separation;
  - same-session serialization without unbounded backlog;
  - latest missed compensation instead of replaying every interval;
  - stale `running` cleanup before compensation;
  - default 10-minute scheduled-run timeout;
  - Worker heartbeat/health with 60-second unhealthy threshold;
  - API/UI timing and health diagnostics;
  - explicit deployability/health checks for scheduled-task Workers.

## Compatibility and runtime constraints to preserve

- Ordinary manual chat behavior must not change.
- Scheduled executions continue to reuse the formal Agent runtime path.
- Skill/MCP permission semantics and Agent capability bindings must not be expanded.
- Same-session scheduled Agent execution remains serialized.
- Notification delivery failure must not re-run Agent or change a successful task run to failed.
- Rollback should be additive: newly stored heartbeat/diagnostic fields can be ignored if the new Worker mode is disabled.

## Known areas to audit during Phase 1

- `apps/api/app/services/scheduled_tasks/scheduler.py`
- `apps/api/app/services/scheduled_tasks/runtime.py`
- `apps/api/app/services/scheduled_tasks/lifecycle.py`
- `apps/api/app/services/scheduled_tasks/notifications.py`
- `apps/api/app/workers/scheduled_tasks.py`
- `apps/api/app/workers/scheduled_task_notifications.py`
- `apps/api/app/routers/agent_chat.py`
- `apps/web/src/views/AgentChat.vue`
- `deploy/docker-compose.yml`
- `deploy/docker-compose.prod.yml`
- `scripts/start-local.sh`
- Existing tests under `apps/api/tests/test_scheduled_task_*.py` and related Agent runtime context tests.

## 2026-10-08 — Task 1.1 audit findings

### Scheduler / executor coupling

- Current Worker entry point `apps/api/app/workers/scheduled_tasks.py::run_once()` performs `create_due_runs()`, `fail_expired_pending_runs()`, `claim_next_run()`, then synchronously calls `execute_and_finalize_claimed_run()`.
- Because `execute_and_finalize_claimed_run()` enters the formal Agent runtime via `asyncio.run(run_agent(...))`, one long Agent execution, LLM retry, MCP retry, or cancellation wait can block the next scheduler scan in the same Worker loop.
- This directly matches the SLA gap in the new change: due-run creation is durable, but not isolated from execution latency.

### Due-run creation / catch-up behavior

- `apps/api/app/services/scheduled_tasks/scheduler.py::create_due_runs()` scans enabled due tasks and creates one `ScheduledTaskRun` with `scheduled_for` and `available_at`.
- Existing occurrence idempotency is based on `(task_id, occurrence_key)`.
- Current source is `"catch_up"` whenever `now > scheduled_for`; this treats even small poll delay as catch-up.
- Current long-downtime handling skips only when `now - scheduled_for > CATCH_UP_WINDOW` and otherwise creates one run then advances `next_run_at` from `now`. It does not yet record skipped/missed window summaries.
- Current `next_run_at` advancement from `now` avoids replaying every interval, but skipped/missed diagnostics are not persisted or visible.

### Lease / stale running behavior

- `claim_next_run()` can reclaim `running` rows whose `lease_expires_at <= now`.
- Reclaimed stale runs are reused as the same `ScheduledTaskRun`, and `started_at` is preserved through `coalesce`.
- There is no persisted Worker heartbeat table and no check that the previous `lease_owner` is unhealthy before reclaiming.
- There is no explicit stale finalization state/reason before compensation. This conflicts with the new requirement that expired running work owned by an unhealthy Worker becomes terminal `failed/stale` instead of being blindly re-executed.

### Same-session serialization and backlog

- `ScheduledTaskSessionSlot` serializes one active scheduled run per session.
- If a session slot is unavailable, `claim_next_run()` rolls back and returns `None`; later runs stay `pending`.
- `fail_expired_pending_runs()` only fails pending rows older than `MAX_QUEUE_AGE`.
- There is no explicit overlap/coalescing policy for periodic occurrences while an earlier same-task or same-session run is active. This can build pending backlog until queue-age failure rather than visible skipped/coalesced history.

### Execution timeout / cancellation

- `execute_claimed_run()` has a persistent cancellation watcher that polls `cancel_requested_at` and calls `stop_chat(...)`.
- There is no outer 10-minute scheduled-task timeout around `run_agent()`.
- Timeout finalization and session-slot release need to reuse lifecycle finalization paths without changing manual chat behavior.

### Notification isolation

- `finish_run_success()` marks the run `succeeded`, creates a notification outbox row when configured, and releases the session slot.
- `deliver_pending()` retries notification delivery independently and, after final failure, creates a session warning message.
- Existing tests verify IM delivery failure does not change the run from `succeeded` and does not rerun Agent. This aligns with the new change and should be preserved.

### API payloads and authorization

- `list_scheduled_tasks` currently returns task payload plus `execution_count`.
- `scheduled_task_progress` and `list_scheduled_task_runs` return run state, steps/result preview, notification state, safe errors, and terminal result payloads.
- Authorization is already session-scoped through `require_session_task_manager()`.
- Missing for the new SLA: Worker health, scheduling delay, executor delay, runtime duration, skipped/missed summary, terminal stale/timeout reasons, and safe per-task last scheduled/started/finished/status diagnostics.

### Frontend status views

- `apps/web/src/views/AgentChat.vue` renders scheduled progress/result cards and polls `scheduled_task_progress`.
- `apps/web/src/components/SessionTickDialog.vue` already shows recent run preview and notification state.
- Missing for the new SLA: Worker health warnings, scheduling/executor delay, skipped/missed counts, stale/timeout reasons, and a clear distinction between "not configured", "not scheduled yet", "queued", "running", "delayed", "timed out", "stale", "failed", "succeeded", and "notification failed".

### Deployment paths

- `deploy/docker-compose.yml` and `deploy/docker-compose.prod.yml` define `scheduled-task-worker` and `scheduled-task-notification-worker` behind the `scheduled-tasks` profile.
- Both workers default disabled via environment (`SCHEDULED_TASKS_WORKER_ENABLED=false`, `SCHEDULED_TASK_NOTIFICATIONS_WORKER_ENABLED=false`).
- `scripts/start-local.sh` defaults `ENABLE_SCHEDULED_TASKS=1` and starts both local workers.
- There is no scheduler/executor/notification role heartbeat or health endpoint that proves the workers are actually alive after startup.

### Historical change comparison

- Archived `2026-09-22-reliable-session-scheduled-tasks` implemented durable tasks/runs, leases, session slots, notification outbox, live progress, stopping, local worker startup, and safe disabled deployment defaults.
- Archived `2026-09-22-reliable-scheduled-task-result-visibility` improved terminal-result visibility and scheduled context shaping.
- The current change does not duplicate or revert those completed designs. It adds the missing SLA/health/timeout/overlap layer on top of them.
- No top-level `openspec/changes/superseded/` directory exists; superseded examples are under `openspec/changes/archive/superseded/`.

### Task 1.1 conclusion

- No duplicate active OpenSpec change conflicts were found; `openspec list --json` reported only `reliable-scheduled-task-sla`.
- The main implementation risks are the synchronous scheduler/executor chain, lack of durable Worker heartbeat, stale running reclaim-by-reexecution, no outer execution timeout, and insufficient SLA diagnostics.
- Task 1.2 should add failing or gap-documenting baseline tests for those exact risks before implementation.

## 2026-10-08 — Task 1.2 baseline regression findings

- Added `apps/api/tests/test_scheduled_task_sla_baseline.py`.
- Baseline cases added:
  - `test_scheduler_pass_does_not_execute_claimed_agent_run`: strict xfail documenting that the current Worker scan still enters Agent execution synchronously.
  - `test_expired_running_run_is_marked_stale_before_compensation_not_reclaimed`: strict xfail documenting that current expired `running` leases can be reclaimed for execution without Worker-health-based stale finalization.
  - `test_task_list_exposes_worker_health_and_timing_diagnostics`: strict xfail documenting missing Worker health and timing diagnostics in task list API.
  - `test_notification_failure_stays_separate_from_successful_run_result`: passing regression that notification delivery failure stays separate from a successful task run and writes a visible session warning.
- Verification command:
  - `cd apps/api && .venv/bin/python -m pytest -q tests/test_scheduled_task_sla_baseline.py`
- Result:
  - `1 passed, 3 xfailed`.
- The strict xfail tests are intentional pre-implementation gap markers. When implementation lands, XPASS will fail the suite until xfail markers are removed or converted to normal passing assertions.

## 2026-10-08 — Task 2.1 Worker heartbeat implementation findings

- Added durable model `ScheduledTaskWorkerHeartbeat` backed by table `scheduled_task_worker_heartbeats`.
- Added `apps/api/app/services/scheduled_tasks/health.py` with:
  - `heartbeat_worker(...)` for privacy-safe role heartbeat upsert;
  - `worker_health(...)` for role health derivation;
  - fixed role set: `scheduler`, `executor`, `notification`;
  - 60-second stale threshold via `HEALTH_STALE_AFTER`.
- Wired current Worker entry points:
  - `apps/api/app/workers/scheduled_tasks.py` records scheduler and executor heartbeats when enabled; shadow mode records scheduler heartbeat only.
  - `apps/api/app/workers/scheduled_task_notifications.py` records notification heartbeat when enabled.
- Disabled state is derived safely from configured role enablement without requiring a running disabled Worker to write to the database.
- Verification command:
  - `cd apps/api && .venv/bin/python -m pytest -q tests/test_scheduled_task_worker_health.py tests/test_scheduled_task_worker_config.py tests/test_scheduled_task_schema.py`
- Result:
  - `14 passed, 1 skipped`.

## 2026-10-08 — Task 2.2 scheduler pass findings

- Added `run_scheduler_pass(...)` in `apps/api/app/services/scheduled_tasks/scheduler.py`.
- The scheduler pass now has a narrow contract:
  - records scheduler heartbeat;
  - creates due runs;
  - fails expired pending queue rows;
  - advances task `next_run_at` through existing `create_due_runs(...)`;
  - returns counts without claiming or executing Agent work.
- Added Worker helpers in `apps/api/app/workers/scheduled_tasks.py`:
  - `run_scheduler_once()` for the lightweight scheduler pass;
  - `run_executor_once()` for claim/execution work;
  - `run_once()` remains backward-compatible and still returns only `created/expired`.
- Added SLA-focused test proving a run scheduled 10 seconds ago is durably created with `available_at - scheduled_for <= 15` and remains `pending`.
- Compatibility fix: an initial implementation returned `claimed` from `run_once()`, which broke the existing worker config contract. The return shape was reverted to `{"created": ..., "expired": ...}`.
- Verification command:
  - `cd apps/api && .venv/bin/python -m pytest -q tests/test_scheduled_task_scheduler.py tests/test_scheduled_task_worker_config.py tests/test_scheduled_task_worker_health.py tests/test_scheduled_task_sla_baseline.py`
- Result:
  - `17 passed, 3 xfailed`.

## 2026-10-08 — Task 2.3 missed-window compensation findings

- Updated `create_due_runs(...)` so downtime recovery creates at most one executable compensation run per task for the latest due window.
- Added a skipped/missed summary row when multiple older windows were missed:
  - `state="skipped"`;
  - `source="missed"`;
  - `error_summary` includes `scheduled_task_missed_windows:<count>`.
- Interval schedules compute the latest due window arithmetically, so a 30-minute outage on a 5-minute interval produces one pending `catch_up` run and one skipped summary, not seven executable runs.
- Cron schedules use bounded scanning (`MAX_CRON_MISSED_SCAN`) to find the latest due window without enqueueing every missed interval.
- Removed the old 24-hour catch-up skip constant because the current requirement is latest compensation with visible missed/skipped recording, not silent discard.
- Verification command:
  - `cd apps/api && .venv/bin/python -m pytest -q tests/test_scheduled_task_scheduler.py tests/test_scheduled_task_worker_config.py tests/test_scheduled_task_sla_baseline.py`
- Result:
  - `14 passed, 3 xfailed`.

## 2026-10-08 — Task 2.4 idempotency and multi-worker safety findings

- Existing uniqueness on `(task_id, occurrence_key)` remains the idempotency boundary for scheduled, catch-up, manual, and missed-summary run rows.
- Added SQLite concurrent due-scan regression:
  - two scheduler sessions scan the same overdue interval task concurrently;
  - exactly one executable compensation run is created;
  - exactly one skipped/missed summary row is created;
  - task `next_run_at` advances beyond the scan time.
- Existing lease-claim concurrency tests continue to prove a single claimant for runnable work.
- Verification command:
  - `cd apps/api && .venv/bin/python -m pytest -q tests/test_scheduled_task_scheduler.py tests/test_scheduled_task_worker_config.py tests/test_scheduled_task_sla_baseline.py`
- Result:
  - `15 passed, 3 xfailed`.

## 2026-10-08 — Task 3.1 executor isolation findings

- Added `scheduled_tasks_worker_role` setting with roles:
  - `scheduler`;
  - `executor`;
  - `combined`.
- Worker main entry now supports separate scheduler-only and executor-only loops.
- In `combined` mode, scheduler and executor run in separate loops/threads so a long executor call does not block scheduler polling in the same process.
- Existing `run_once()` remains available for compatibility and still returns the legacy scheduler count shape.
- Same-session serialization remains enforced by `ScheduledTaskSessionSlot` in `claim_next_run(...)`.
- Added regression proving `run_scheduler_once()` creates a due run while `run_executor_once()` is blocked inside a long executor call.
- Verification command:
  - `cd apps/api && .venv/bin/python -m pytest -q tests/test_scheduled_task_scheduler.py tests/test_scheduled_task_worker_config.py tests/test_scheduled_task_worker_health.py tests/test_scheduled_task_sla_baseline.py`
- Result:
  - `21 passed, 3 xfailed`.

## 2026-10-08 — Task 3.2 stale running cleanup findings

- `claim_next_run(...)` no longer reclaims expired `running` runs for execution.
- Added `finalize_stale_running_runs(...)`:
  - scans expired `running` runs;
  - checks executor heartbeat by `lease_owner`;
  - marks runs owned by missing/disabled/stale Workers as `failed`;
  - records `error_summary="scheduled_task_stale_worker"`;
  - clears lease owner/expiry;
  - releases the session slot only when it is still owned by the stale run.
- `run_scheduler_pass(...)` now performs stale cleanup before latest-missed compensation and due-run creation.
- Added regressions proving:
  - an expired running run is not reclaimed for execution;
  - stale cleanup releases the session slot and creates a new latest compensation run;
  - a run owned by a healthy executor heartbeat is not finalized as stale.
- Verification command:
  - `cd apps/api && .venv/bin/python -m pytest -q tests/test_scheduled_task_scheduler.py tests/test_scheduled_task_worker_health.py tests/test_scheduled_task_sla_baseline.py`
- Result:
  - `19 passed, 3 xfailed`.

## 2026-10-08 — Task 3.3 scheduled execution timeout findings

- Added `scheduled_task_execution_timeout_seconds` setting with default `600.0`.
- Wrapped scheduled `run_agent(...)` execution in `asyncio.wait_for(...)` inside scheduled-task runtime only.
- On scheduled timeout:
  - calls existing `stop_chat(...)` cancellation path;
  - raises `scheduled_task_execution_timeout`;
  - Worker finalization marks the run `failed`;
  - timeout is not classified as a transient retry;
  - session slot is released;
  - notification outbox is not created.
- Ordinary manual chat runtime path is not changed; timeout is applied only through `apps/api/app/services/scheduled_tasks/runtime.py`.
- Verification command:
  - `cd apps/api && .venv/bin/python -m pytest -q tests/test_scheduled_task_runtime.py tests/test_scheduled_task_scheduler.py tests/test_scheduled_task_worker_config.py`
- Result:
  - `29 passed`.

## 2026-10-08 — Task 3.4 bounded overlap findings

- Added active overlap detection during due-run creation.
- If the same session already has a `pending` or `running` scheduled run, the new due occurrence is recorded as:
  - `state="skipped"`;
  - `source="coalesced"`;
  - `error_summary="scheduled_task_overlap_coalesced"`.
- The task `next_run_at` is still advanced, preventing repeated scans from building an unbounded backlog.
- Added regressions for:
  - active same-task overlap;
  - active same-session overlap from another task;
  - concurrent due scans with one executable compensation and one missed summary, allowing additional coalesced visibility without duplicate executable work.
- Verification command:
  - `cd apps/api && .venv/bin/python -m pytest -q tests/test_scheduled_task_scheduler.py tests/test_scheduled_task_runtime.py tests/test_scheduled_task_sla_baseline.py`
- Result:
  - `28 passed, 3 xfailed`.

## 2026-10-08 — Task 3.5 notification/result separation findings

- No additional code change was required for notification/result separation in this task.
- Existing flow remains:
  - successful scheduled Agent writeback marks the run `succeeded`;
  - success creates notification outbox only after conversation result is durable;
  - notification worker retries independently;
  - final notification failure writes a visible session warning and leaves the run `succeeded`;
  - notification outbox failures do not re-run Agent.
- Timeout and no-progress failure paths do not create notification outbox entries.
- Verification command:
  - `cd apps/api && .venv/bin/python -m pytest -q tests/test_scheduled_task_notifications.py tests/test_scheduled_task_results.py tests/test_scheduled_task_runtime.py tests/test_scheduled_task_scheduler.py tests/test_scheduled_task_sla_baseline.py`
- Result:
  - `32 passed, 3 xfailed`.

## 2026-10-08 — Task 4.1 API diagnostics findings

- Extended scheduled-task APIs with safe diagnostics:
  - task list: `next_run`, last scheduled/started/finished/status, scheduling delay, executor delay, runtime duration, skipped/missed/coalesced summary, and Worker health;
  - progress endpoint: scheduled/available times, timing diagnostics, and Worker health;
  - run history: available time, timing diagnostics, and Worker health.
- `serialize_scheduled_run_result(...)` now includes `available_at`, scheduling delay, executor delay, and runtime duration.
- Added `run_diagnostics(...)` and `skipped_missed_summary(...)` helpers to keep diagnostics bounded and safe.
- Authorization remains enforced through existing session-scoped `require_session_task_manager(...)`; tests verify unrelated users cannot read diagnostic/result payloads.
- Verification command:
  - `cd apps/api && .venv/bin/python -m pytest -q tests/test_scheduled_task_authorization.py tests/test_scheduled_task_results.py tests/test_scheduled_task_sla_baseline.py tests/test_scheduled_task_progress.py`
- Result:
  - `12 passed, 2 xfailed`.

## 2026-10-08 — Task 4.2 UI diagnostics findings

- Updated `SessionTickDialog.vue` to display:
  - Worker health warnings;
  - task-level last status and delay diagnostics;
  - skipped/missed/coalesced summary;
  - terminal timeout/stale/coalesced reasons;
  - run-level timing diagnostics;
  - existing notification failure labels.
- Updated `AgentChat.vue` scheduled progress card to show timing diagnostics and Worker warnings.
- Rendering uses optional chaining and default fallbacks, so older rows without new diagnostics render safely.
- Added frontend static assertions in `test_scheduled_task_frontend_ui.py`.
- Verification command:
  - `cd apps/api && .venv/bin/python -m pytest -q tests/test_scheduled_task_frontend_ui.py tests/test_scheduled_task_authorization.py tests/test_scheduled_task_sla_baseline.py`
- Result:
  - `10 passed, 2 xfailed`.

## 2026-10-08 — Task 4.3 deployment diagnostics findings

- Docker Compose and production Compose now declare explicit scheduled-task roles:
  - `scheduled-task-scheduler`;
  - `scheduled-task-executor`;
  - `scheduled-task-notification-worker`.
- Scheduler and executor services set `SCHEDULED_TASKS_WORKER_ROLE` explicitly.
- Scheduled execution timeout is configurable with `SCHEDULED_TASK_EXECUTION_TIMEOUT_SECONDS`, defaulting to 600 seconds.
- Local start script now starts separate scheduler and executor processes with separate pid/log files.
- Local stop script stops new scheduler/executor pid files and still keeps the old `scheduled-tasks` pid cleanup for compatibility.
- Default-safe behavior remains disabled unless `SCHEDULED_TASKS_WORKER_ENABLED` / notification enabled flags are set.
- Verification command:
  - `cd apps/api && .venv/bin/python -m pytest -q tests/test_scheduled_task_worker_config.py tests/test_scheduled_task_schema.py tests/test_scheduled_task_worker_health.py`
- Result:
  - `16 passed, 1 skipped`.

## 2026-10-08 — Task 5.1 backend regression findings

- Ran all scheduled-task backend suites:
  - scheduler;
  - runtime;
  - lifecycle behavior covered through scheduler/runtime tests;
  - notifications;
  - authorization;
  - results;
  - worker config;
  - startup/schema migration;
  - progress;
  - Agent runtime scheduled context;
  - frontend static scheduled-task checks housed in API tests.
- Converted the original baseline xfail cases into normal passing regressions after the implementation closed the gaps.
- Compatibility notes:
  - manual chat runtime path is unchanged by scheduled timeout because timeout wraps only scheduled-task runtime;
  - Agent Skill/MCP bindings continue through the existing `run_agent` path;
  - notification failure remains isolated from run success;
  - disabled Worker mode still returns safe no-op responses.
- Verification command:
  - `cd apps/api && .venv/bin/python -m pytest -q tests/test_scheduled_task_*.py`
- Result:
  - `68 passed, 1 skipped`.

## 2026-10-08 — Task 5.2 frontend/build findings

- Ran affected frontend build after scheduled-task UI and AgentChat diagnostics changes.
- Verification command:
  - `cd apps/web && npm run build`
- Result:
  - build succeeded.
- Warnings:
  - existing Rollup/Vite warnings about `/* #__PURE__ */` comments in `@vueuse/core`;
  - existing chunk size warnings for large bundled assets.
- Ordinary conversation display risk:
  - scheduled diagnostics are gated to scheduled-task UI/progress surfaces;
  - ordinary assistant/user message rendering code paths were not changed.

## 2026-10-08 — Task 5.3 final verification findings

- OpenSpec validation passed:
  - `openspec validate reliable-scheduled-task-sla --strict`
  - result: `Change 'reliable-scheduled-task-sla' is valid`
- Diff hygiene passed:
  - `git diff --check`
  - result: no whitespace errors.
- Final OpenSpec task status before checking 5.3:
  - 16/17 complete, only 5.3 pending.
- Implementation compatibility:
  - ordinary manual chat path is not wrapped by the scheduled-task timeout;
  - scheduled executions still use the formal Agent runtime and existing Skill/MCP binding semantics;
  - Agents without configured channels still write session results; notifications remain optional;
  - notification failure remains independent from successful session writeback;
  - default Worker flags remain safe-disabled unless explicitly enabled.
- Idempotency:
  - due-run creation continues to use `(task_id, occurrence_key)` uniqueness;
  - concurrent scheduler tests cover one compensation run and one missed summary under SQLite;
  - claim tests preserve single executor ownership.
- Rollback:
  - heartbeat table and diagnostics are additive;
  - explicit Worker roles can be disabled with existing `SCHEDULED_TASKS_WORKER_ENABLED=false`;
  - UI safely ignores missing diagnostics on older rows.
- Known warnings accepted:
  - Pydantic/SQLAlchemy datetime deprecation warnings in tests;
  - Vite/Rollup chunk-size and comment warnings during frontend build.

## 2026-10-08 — opsx:verify verification findings

- Verified OpenSpec status:
  - `openspec status --change reliable-scheduled-task-sla --json` reports schema `spec-driven` and all artifacts present.
  - `openspec instructions apply --change reliable-scheduled-task-sla --json` reports `17/17` tasks complete and `state: all_done`.
- Verified implementation coverage against delta spec:
  - scheduling SLA and lightweight scheduler pass are implemented in `apps/api/app/services/scheduled_tasks/scheduler.py`;
  - durable Worker heartbeat and 60-second unhealthy detection are implemented in `apps/api/app/services/scheduled_tasks/health.py`;
  - stale running cleanup, missed-window compensation, and overlap coalescing are covered in scheduler tests;
  - scheduled execution timeout is implemented in `apps/api/app/services/scheduled_tasks/runtime.py` and classified in lifecycle;
  - API diagnostics are exposed from `apps/api/app/routers/agent_chat.py` and serializers;
  - UI diagnostics are rendered in `apps/web/src/components/SessionTickDialog.vue` and `apps/web/src/views/AgentChat.vue`;
  - local and Docker deployment split scheduler, executor, and notification Workers explicitly.
- Verification commands:
  - `openspec validate reliable-scheduled-task-sla --strict`
  - `git diff --check`
  - `cd apps/api && .venv/bin/python -m pytest -q tests/test_scheduled_task_*.py`
  - `cd apps/web && npm run build`
- Results:
  - OpenSpec strict validation passed;
  - diff hygiene passed;
  - backend scheduled-task suite passed: `68 passed, 1 skipped`;
  - frontend production build succeeded.
- Compatibility and rollback:
  - manual chat is not wrapped by the scheduled-task timeout;
  - Skill/MCP permissions and Agent bindings still flow through the existing Agent runtime path;
  - notification failure remains separate from run success and does not trigger Agent re-execution;
  - heartbeat table/API/UI diagnostics are additive and safe for old rows;
  - rollback can disable scheduled Workers via existing worker enablement flags while retaining readable durable task/run records.
- No CRITICAL or WARNING findings remain for this change. Existing third-party build/test warnings are accepted and unrelated to this change.

## Error Log

| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-10-08 | Prior Planning with Files hook injected archived `react-engine-unified-output` plan | Session start | Reinitialized root planning files and `.planning/.active_plan` for `reliable-scheduled-task-sla`. |
