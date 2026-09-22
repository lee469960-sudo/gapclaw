# Findings: reliable-session-scheduled-tasks

## Initial baseline

- Existing `AgentTick` is loaded by an API-process-local APScheduler and invokes Agent execution directly.
- Existing configuration and UI establish a useful migration source, but no execution ledger records start, success, failure, skip, attempts, output association or notification delivery.
- Process-local scheduling can duplicate work across API replicas and has no durable recovery contract for restart, lease ownership or missed triggers.
- Existing paths include both session-oriented Tick APIs and legacy Tick APIs; the latter must be inventoried before changes so there is exactly one executor after migration.
- Existing tasks do not persist a user-selectable timezone; migrated records require the agreed default `Asia/Shanghai`.

## Decisions already fixed by the OpenSpec change

- Tasks are session-bound and user-owned; editors may manage them.
- Scheduling supports one-time, interval and Cron forms, with searchable IANA timezone selection and `Asia/Shanghai` default.
- A separate Scheduler Worker uses database leases and occurrence idempotency; Agent work is serial per session.
- Agent success requires durable writeback to the original conversation; notifications are an independent retried outbox concern.

## Discovery log

| Date | OpenSpec task | Finding | Impact / follow-up |
|---|---|---|---|
| 2026-09-20 | initialization | Planning initialized after proposal/design/spec/task review. | Start task 1.1; append implementation evidence here as work proceeds. |
| 2026-09-20 | 1.1 | Completed Tick inventory and compatibility map. | New APIs must replace every enumerated write entry before the new executor is enabled. |
| 2026-09-20 | 1.2 | Added durable task, run and notification-delivery persistence tables. | New tables are created by the existing `Base.metadata.create_all` startup convention; legacy-row migration remains task 1.3. |
| 2026-09-20 | 1.3 | Implemented idempotent legacy Tick migration. | Valid legacy rows become disabled-by-default-until-worker tasks only when the new executor feature is later enabled; invalid rows remain disabled with an audit reason. |

## Legacy Tick replacement and compatibility map

| Current path | Current behavior | Replacement / compatibility disposition |
|---|---|---|
| `agent_chat.py`: `add_tick` | Creates `AgentTick`, validates Agent/session membership, and immediately registers an in-process APScheduler job. | Replace with session-scoped scheduled-task creation; persist first and let only the new Worker execute when enabled. |
| `agent_chat.py`: `update_tick` | Mutates Cron/message/enabled and removes/re-adds the process-local job. | Replace with authorized task update and durable next-run recomputation; never call the legacy scheduler. |
| `agent_chat.py`: `toggle_tick` / `delete_tick` | Looks up by `tick_id` alone and mutates/removes live jobs. | Replace with scope-authorized enable/soft-delete and cancellation of only pending runs. |
| `agent.py`: `toggle_tick` / `delete_tick` | Legacy writes mutate rows without scheduler synchronization or session/user scope checks. | Deprecate as non-writing compatibility endpoints; return mapped read/deprecation behavior after migration. |
| `agent.py`: `delete_session` | Hard-deletes rows and removes legacy jobs while deleting session content. | Adapt to disable/soft-delete scheduled tasks and cancel pending runs before session removal while retaining audit history. |
| `agent.py`: `list_ticks` / `list_all_ticks`; `agent_chat.py`: `list_ticks` | Two overlapping read APIs; only the session route validates Agent/session membership. | Consolidate reads behind session-scoped task/history APIs; keep legacy reads mapping-only during compatibility period. |
| `tick_scheduler.py` / `main.py` lifespan | Every API process loads enabled rows into APScheduler; busy or manually stopped sessions are logged and silently skipped. | Replace as the sole executor with feature-gated Scheduler Worker; old scheduler is disabled before new execution authority is enabled. |
| `SessionTickDialog.vue`, `Agents.vue` | Current consumers of session CRUD and global legacy list. | Migrate to unified task APIs; preserve only explicitly supported compatibility reads. |
| `channels/runtime.py` and `release_lifecycle.py` | Provide `ChannelAdapter.send_text` and an existing durable release delivery pattern. | Reuse the adapter boundary for a separate scheduled-task notification outbox; do not send from Agent execution transaction. |

## Verification evidence

- `rg` found the only web mutation consumer in `SessionTickDialog.vue` and the global legacy list consumer in `Agents.vue`.
- `rg` found all server Tick writes: session `add_tick`, `update_tick`, `toggle_tick`, `delete_tick`; legacy `toggle_tick`, `delete_tick`; and `delete_session` bulk cleanup.
- `main.py` owns `start_scheduler`/`stop_scheduler`; `startup.py` uses `Base.metadata.create_all`, so persistence migration must follow the project startup migration conventions rather than assume Alembic exists.
- Channel delivery is available through `ChannelAdapter.send_text`; release notification delivery is a relevant auditable-outbox precedent.
- `ScheduledTaskRun` has a database-level unique `(task_id, occurrence_key)` constraint; this is the cross-worker idempotency anchor for later claiming logic.
- The scheduled-task models use timezone-aware database datetimes for scheduling, leases and lifecycle timestamps, while retaining existing string identifiers and JSON-text conventions for compatibility.
- SQLite startup upgrade was tested against an existing `agent_ticks`-only database via `init_db`; PostgreSQL table creation was tested in an isolated temporary container and then removed.
- Legacy migration uses `ScheduledTask.legacy_agent_tick_id` with a unique constraint. A valid row requires an existing Agent, a session present in that Agent's session list, a non-empty legacy creator, and a valid five-field Cron expression.
- Startup creates an upgrade column/index for an early `scheduled_tasks` table lacking the legacy anchor before scanning old rows.
- The original task 1.4 was reordered to task 3.5 on 2026-09-21: the API image has only a `uvicorn` command, so deployment wiring must follow runnable Scheduler/notification Worker implementation.
- Agent `allowed_users` is the existing collaborator representation. The new authorization boundary treats it as task-editor access while deliberately not treating public Agent visibility as task management authority.
- Legacy Tick writes now return `legacy_tick_write_deprecated`; compatibility reads source `ScheduledTask`, so the retained APScheduler code has no API path that can create or reschedule new work.
- Due-instance creation advances `ScheduledTask.next_run_at` transactionally after attempting the unique occurrence insert. The `(task_id, occurrence_key)` constraint makes duplicate scans/restart recovery idempotent, and missed executions older than 24 hours are advanced without replay.
- SQLite returns persisted timezone-aware datetimes as naive values despite the model declaration. Scheduler comparisons normalize those values to UTC at the storage boundary; PostgreSQL retains its native timezone behavior.
- PostgreSQL claims use `FOR UPDATE SKIP LOCKED` before the final conditional update. SQLite does not support that lock clause, so the same conditional `UPDATE` is the authoritative one-claimant gate on both dialects; the SQLite file-backed concurrent regression confirms the behavior.
- Same-session exclusion requires a separate durable slot keyed by `session_id`; filtering only `ScheduledTaskRun` rows cannot prevent two workers from claiming different tasks in the same session. The selected max queue age is one hour, and expiration is recorded as `scheduled_task_queue_age_exceeded` rather than silently dropped.
- The public execution-state contract excludes a separate `leased` value. A claimed run is therefore recorded as `running`, while its `lease_owner` and `lease_expires_at` retain the Worker coordination detail and allow recovery after expiry.
- Retry classification is intentionally narrow: connection/timeout/transient-unavailability/rate-limit conditions retry at most three times with 1/2 minute backoff; other failures are business failures and terminal after one attempt. Task disable/delete only changes `pending` runs to `cancelled`, preserving in-flight work and audit history.
- Both Workers are Compose `scheduled-tasks` profile services and have disabled-by-default environment flags. The Scheduler is deliberately scan-only until task 4 wires formal session execution/writeback; shadow mode makes no durable changes, avoiding premature claims. Compose sets the Scheduler's single-executor flag to true.
- Scheduler observability uses an explicit allowlist. Events cover occurrence, lease, queue, retry and terminal lifecycle data but cannot contain prompt/message, credentials, raw errors or model output.
- The existing formal session runtime accepts `message_meta`; the scheduled adapter uses it to mark source/run ID while delegating all Agent, model, Skill and MCP selection to the unchanged `run_agent` code path.
- Assistant-message persistence previously copied only a fixed metadata subset. It now includes only the scheduled run identifiers needed to find the exact output. A scheduled run is finalized only after that message is bound; an absent write leaves the run non-successful.
- Recovery rejects runs that already have a bound message or are terminal. This prevents duplicate session injection after a retry/restart; arbitrary external tool side effects before durable binding remain a documented residual risk.
- Compatibility verification passed for legacy Tick/session scope, MCP routing/session management, Skill payload routing and runtime resume/dedup. Real configured IM provider credentials were not available locally; the mock delivery outbox covers isolation/retry behavior, while live-provider delivery remains a rollout canary check.
- Local Compose confirms Workers are profile-gated and disabled by default; staging enable/disable and real-provider delivery require deployment credentials and are not represented as completed local verification.
- Actual local diagnosis confirms that a successful run creates an independent pending notification outbox row; API/UI startup alone cannot drain it. `scripts/start-local.sh` now owns both local Worker processes by default (opt out with `ENABLE_SCHEDULED_TASKS=0`) and `stop-local.sh` stops them. The Worker entrypoints initialize the schema with an explicit database session, avoiding the prior `init_db()` call error.
- A notification destination is an internal Agent session ID, not a Telegram `chat_id`. Delivery resolves it only through an `ImSession` matching both selected channel and that exact session. The diagnosed `okx-trader` task session had no mapping, while the selected Telegram channel was bound to another Agent/session. Falling back to the channel's only known chat would leak output across conversations, so that fallback is prohibited; the channel must instead be bound and used with the intended Agent/session.
- `ScheduledTaskRun.attempt` is a retry counter, not an execution counter: it is intentionally zero on a successful first try. The session task list now returns `execution_count`, counting runs in `running`, `succeeded`, or `failed` state, and the UI displays it independently from `重试 N/3`.
- Local Cron diagnosis found automatic runs were never created for the `okx-trader` task because `next_run_at` for timezone Cron schedules was persisted as the schedule timezone's wall-clock value while the Worker compared due tasks against UTC. Immediate/manual runs therefore worked, but `*/10 * * * *` in `Asia/Shanghai` could be delayed by eight hours. `next_run_at` is now normalized to UTC on create/update and startup recomputes enabled tasks to repair existing rows.
- Scheduled-task notification diagnosis found two separate issues: delivery sent a fixed `"定时任务已完成"` placeholder instead of the associated conversation result, and Telegram delivery used the Agent session id as the chat id when no exact `ImSession.agent_session_id` mapping existed. The selected Telegram channel had been rebound to the target Agent, but its sole known external chat row was still stored under the old Agent/session until the next inbound message. Delivery now sends the bound `ChatMessage.content`, resolves exact session mappings first, and only falls back to the channel's sole external chat when the channel is currently bound to the scheduled task's Agent.
- Local verification on 2026-09-21 found `ca48d98e` stuck in `running` after its five-minute lease expired while `scheduled-task` Worker pid files were stale/dead. A one-shot Worker scan reclaimed and completed it (`chat_message_id=2669`), and the notification outbox reached `delivered`. The API process alone does not execute or drain these Workers; local startup must keep both Worker processes alive, and stale pid files must not be treated as proof of liveness.
- The operational runbook now requires a real staging shadow/canary/disable-enable exercise before task 6.3 can close. Local Compose parsing and SQLite regression are evidence for configuration and implementation only; they cannot prove the deployed Worker process remains alive or that there is no staging duplicate execution.

## Errors and failed attempts

本次调查：ChatStreamHub 与 is_running 都是进程内状态，独立 Worker 发布步骤无法抵达 API WebSocket。用跨进程数据库进度快照与会话内独立进度卡解决；之前仅展示任务计划的方案已撤销。删除功能保持撤销。

| Attempt | Error | Resolution |
|---|---|---|
| Inventory command 1 | zsh rejected an unmatched compose-file glob because no matching file existed. | Replaced it with scoped `rg` searches. |
| Inventory command 2 | zsh rejected nested quote syntax. | Used simple literal `rg` expressions; inventory completed. |
| Schema test command 1 | pytest path was relative to the wrong working directory. | Re-ran `tests/test_scheduled_task_schema.py` from `apps/api`; tests passed. |
| PostgreSQL test command 1 | Sandbox denied localhost TCP access to the isolated test container. | Re-ran the same isolated test with approved elevated local-network access; it passed. |
| OpenSpec task 1.4 | Deployment wiring required a Worker command that did not exist until tasks 3.1–3.4. | Reordered as task 3.5; do not add a placeholder production worker. |
| Scheduler test attempt 1 | SQLite returned `ScheduledTask.next_run_at` without `tzinfo`, causing subtraction from an aware UTC worker clock to raise `TypeError`. | Added `_as_utc` normalization at the scheduler persistence boundary and a catch-up regression test. |
| Lease test attempt 1 | SQLAlchemy tried to synchronize an in-memory SQLite identity using a naive persisted expiration against an aware clock, raising `TypeError` before executing the conditional update. | Set `synchronize_session=False`; the database is the lease authority and the result is reloaded after commit. |
| Design search attempt | Looked for extensionless `design`/`proposal` paths and `rg` reported them absent. | Re-ran the search against `design.md` and `proposal.md`; no code or plan impact. |
| Worker Compose validation attempt | Ran relative `deploy/docker-compose.yml` from `apps/api`, so Docker could not find the file. | Re-ran from the repository root; both local and production Compose configurations expanded successfully. |
| Three-item rollback scan | Ran repository-root relative paths while the working directory was `apps/api`, so `rg` reported those paths absent. | Re-ran from the repository root; no rollback-specific symbols remained. |
| Three-item rollback web build | Ran `npm run build` at the repository root, which has no `package.json`. | Re-ran from `apps/web`; production build passed. |
