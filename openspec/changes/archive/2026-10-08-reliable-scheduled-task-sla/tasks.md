## 1. Audit and observability baseline

- [x] 1.1 Audit current scheduled-task scheduler, executor, notification Worker, API payloads, frontend status views, Docker/local deployment paths, and recent archived changes; record findings and verify no duplicate design conflicts are introduced.
- [x] 1.2 Add baseline regression tests that reproduce scheduler scan blocking while Agent execution is slow, stale `running` lease behavior, missing Worker health visibility, and notification failure isolation; verify these tests fail or clearly document the current gap before implementation.

## 2. Scheduler SLA and Worker health

- [x] 2.1 Add durable Worker heartbeat/role state for scheduler, executor, and notification Workers with 60-second unhealthy detection; verify unit tests cover healthy, stale, missing, and disabled Worker states.
- [x] 2.2 Refactor due-occurrence creation so the scheduler pass updates heartbeat, creates due/skipped/missed runs, advances `next_run_at`, and returns without waiting for Agent execution; verify due runs are created within the 15-second SLA in tests with a blocked executor.
- [x] 2.3 Implement latest-missed compensation and skipped/missed recording for multi-window downtime; verify recovery creates at most one compensation run per task and does not enqueue every missed interval.
- [x] 2.4 Preserve idempotency and multi-worker safety for due-run creation under concurrent schedulers; verify unique occurrence tests and Postgres/SQLite-compatible claim behavior still pass.

## 3. Executor isolation, timeout, and stale cleanup

- [x] 3.1 Split or isolate executor processing from scheduler scanning in Worker entry points while keeping same-session execution serialized; verify scheduler heartbeat and due-run creation continue while an executor is running a long task.
- [x] 3.2 Add stale `running` detection that finalizes expired runs owned by unhealthy Workers as failed/stale and releases the session slot safely; verify stale cleanup does not re-execute old work and allows the latest compensation run to proceed.
- [x] 3.3 Add the default 10-minute scheduled-task execution timeout using the existing cancellation/stop path where possible; verify timeout finalizes as `failed` with a timeout reason, releases the session slot, and leaves ordinary manual chat behavior unchanged.
- [x] 3.4 Implement bounded overlap handling for repeated periodic tasks and same-session runs so active overlap is skipped or coalesced instead of building an unbounded backlog; verify skipped/coalesced occurrences are visible in run history.
- [x] 3.5 Preserve notification/result separation so successful session writeback remains `succeeded` when notification delivery fails; verify notification outbox failures do not re-run Agent and do not change task success state.

## 4. API, UI, and deployment diagnostics

- [x] 4.1 Extend scheduled-task list, progress, and run-history APIs with safe diagnostics: `next_run`, `last_scheduled`, `last_started`, `last_finished`, `last_status`, scheduling delay, executor delay, runtime duration, skipped/missed summary, and Worker health; verify authorization tests prevent leaking diagnostics outside the session.
- [x] 4.2 Update session scheduled-task UI to show Worker health warnings, delay diagnostics, terminal status, skipped/missed counts, timeout/stale reasons, and notification failure warnings; verify frontend/static tests cover the new fields and old rows without diagnostics still render safely.
- [x] 4.3 Update local and Docker/cloud deployment configuration so scheduled-task scheduler/executor/notification Workers are explicit, enablement is documented, and missing Workers are health-checkable; verify config tests cover default-safe disabled mode and enabled deployment mode.

## 5. Regression and verification

- [x] 5.1 Run affected backend scheduled-task suites covering scheduler, runtime, lifecycle, notifications, authorization, results, worker config, startup migrations, and Agent runtime context; verify all pass and record any compatibility risks.
- [x] 5.2 Run affected frontend/build checks for scheduled-task UI and AgentChat regressions; verify existing ordinary conversation display remains unchanged.
- [x] 5.3 Run OpenSpec validation with `openspec validate reliable-scheduled-task-sla --strict` and repository diff checks; verify implementation, regression tests, compatibility, idempotency, and rollback findings before marking the change complete.
