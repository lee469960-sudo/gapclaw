## 1. Foundation and data migration

- [x] 1.1 Inventory the existing `AgentTick`, tick routes, scheduler startup, session authorization and channel-delivery paths; record the concrete replacement/compatibility map in the change findings and verify every legacy write path is accounted for.
- [x] 1.2 Add task, execution and notification-delivery persistence models with ownership, schedule parameters, timezone, soft deletion, occurrence uniqueness, state transitions, lease metadata and message/run references; verify migration upgrade succeeds on SQLite and PostgreSQL.
- [x] 1.3 Implement migration of attributable legacy Tick records with `Asia/Shanghai` default and auditable disabling of unresolvable records; verify fixture migration preserves valid schedules and creates no executable duplicate.

## 2. Task configuration, authorization and compatibility API

- [x] 2.1 Implement a single session-scoped authorization helper for task configuration, run history, immediate run and retry; verify owner, editor, read-only collaborator and unrelated-user cases with API tests.
- [x] 2.2 Implement session-scoped task create/read/update/enable/disable/soft-delete APIs for one-time, interval and Cron schedules; verify schema validation for IANA timezone, Cron syntax, five-minute minimum and task quotas.
- [x] 2.3 Implement next-run calculation and public task summaries without relying on process-local scheduler state; verify DST and representative IANA timezone cases.
- [x] 2.4 Convert legacy Tick write routes to an explicit deprecation response and map their read behavior to migrated tasks; verify no legacy route can start, reschedule or duplicate execution.
- [x] 2.5 Implement task-run and notification-history APIs with safe error summaries and pagination; verify an unauthorized caller cannot enumerate known task or run identifiers.

## 3. Reliable Scheduler Worker

- [x] 3.1 Implement due-instance creation, occurrence idempotency and one-window catch-up calculation; verify duplicate scans and restart recovery create at most one run per scheduled occurrence.
- [x] 3.2 Implement database lease claiming and lease-expiry recovery with PostgreSQL locking and SQLite-compatible conditional updates; verify concurrent worker tests allow exactly one claimant.
- [x] 3.3 Implement per-session execution slots, deterministic pending ordering and maximum queue-age failure; verify two due tasks in one session run sequentially and an expired queued run is visibly terminal.
- [x] 3.4 Implement run state transitions, transient-error classification, three-attempt exponential backoff and cancellation of unstarted work; verify business failure is not retried and disabling/deleting cancels pending work only.
- [x] 3.5 Add configuration flags and deployment wiring for the Scheduler and notification workers, including shadow mode and a single-executor guard; verify all flags default to legacy-safe disabled behavior.
- [x] 3.6 Add structured metrics and logs for occurrence creation, lease ownership, queue delay, catch-up, retries, terminal state and lease recovery; verify they exclude prompts, credentials and full model output.

## 4. Session runtime and notification integration

- [x] 4.1 Invoke the existing formal session Agent execution path from a claimed run with source metadata and optional immutable configuration snapshot; verify normal chat, unbound Skill/MCP Agents and bound-tool Agents preserve their existing behavior.
- [x] 4.2 Atomically associate the Agent run and durable conversation message with the scheduled run before marking success; verify a successful Agent response is visible in the original session and a write failure cannot be reported as success.
- [x] 4.3 Handle runtime cancellation, process crash and resume boundaries without injecting a second trigger for an already-associated run; verify checkpoint/resume regression tests and documented residual external-side-effect risk.
- [x] 4.4 Implement notification outbox creation after successful conversation writeback and a separate retrying delivery worker; verify IM delivery failure does not rerun the Agent and final delivery failure creates a visible session warning.

## 5. Conversation UI

- [x] 5.1 Extend the session task dialog with one-time, interval and Cron modes, a searchable full IANA timezone dropdown defaulting to `Asia/Shanghai`, snapshot selection and optional notification settings; verify client-side validation matches API errors.
- [x] 5.2 Display next run, task state, recent executions, queue/retry state, associated output and safe failure details in the originating conversation; verify loading, empty, failed and unauthorized states.
- [x] 5.3 Add immediate run, enable/disable, soft delete and final-failure retry controls that call the unified session APIs; verify immediate run creates the same auditable queued instance as scheduled work.

- [x] 5.4 Intercept blank scheduled triggers and empty successful outputs; persist Worker execution steps and expose authorized live progress in the conversation, including refresh/reconnect and terminal history reload. Verify process-separated progress, empty execution and compatibility regressions. No output deletion.
- [x] 5.5 Implement an authorized persistent stop-current-run action: disable the periodic task, cancel pending instances, signal the independent Worker to stop the active Agent at a cancellable boundary, record `cancelled`, release the session slot, and suppress success notification. Verify API, Worker/runtime and originating-session UI behavior without changing ordinary chat cancellation.

## 6. Rollout, regression and operational verification

- [x] 6.1 Add API, migration, worker, runtime, channel and frontend end-to-end tests covering authorization, timezones/DST, quotas, multi-worker claims, restart catch-up, ordering, retry, cancellation, message writeback and notification isolation; verify affected suites pass on SQLite and PostgreSQL.
- [x] 6.2 Run regression coverage for ordinary conversations, existing Agents, Agent Skill/MCP bindings, MCP connection/retry/cache behavior, checkpoint resume and configured IM channels; record results and known limitations in `progress.md`.
- [x] 6.3 Document shadow-mode rollout dashboards, canary success criteria, on-call diagnosis and the no-overlap rollback procedure; verify the user-approved local Docker + PostgreSQL staging-equivalent deployment can enable and disable the new executor without duplicate runs. Production rollout still requires the same runbook on its isolated staging environment.
- [x] 6.4 Run `openspec validate reliable-session-scheduled-tasks --strict` and the project `opsx:verify` process (implementation, regression, compatibility and rollback risk); verify all findings are resolved or explicitly accepted before archive.
