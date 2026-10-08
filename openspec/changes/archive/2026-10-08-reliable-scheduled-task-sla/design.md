## Context

See `proposal.md` for motivation. Current scheduled-task behavior already has durable tasks, runs, leases, session slots, notification outbox, and session result writeback. The reliability gap is that the Worker loop can synchronously execute Agent work after creating due runs, so a long or retrying Agent run can delay future scans. Worker enablement is also opt-in and not visible enough to users, making a configured task look idle when no scheduler/executor is actually healthy.

Existing main behavior to preserve:

- Session ownership and authorization remain the access boundary.
- Same-session scheduled Agent executions remain serialized.
- Successful session writeback remains separate from notification delivery.
- Scheduled executions reuse the existing Agent runtime path; ordinary chat behavior must not change.
- Notification failures must not cause Agent re-execution.

## Goals / Non-Goals

**Goals:**

- Make due-run creation lightweight and able to meet the 15-second scheduling SLA while a healthy scheduler Worker is active.
- Separate scheduler, executor, and notification responsibilities enough that slow Agent execution or delivery does not block discovery of due work.
- Prevent backlog snowballing for periodic tasks by coalescing or skipping overlapping occurrences.
- Finalize stale and timed-out runs safely so session slots are released and future runs can proceed.
- Expose Worker health and delay diagnostics in API/UI so operators can tell whether a task is scheduled, queued, running, delayed, unhealthy, or notification-failed.
- Keep rollback simple: disable the new Worker mode and fall back to existing durable run records without migrating user task definitions.

**Non-Goals:**

- No guarantee that an Agent result completes by the scheduled time.
- No parallel scheduled execution in the same session.
- No change to Skill/MCP permission semantics or Agent capability bindings.
- No retry of business failures or no-progress failures.
- No attempt to replay every missed interval after downtime.

## Decisions

### 1. Split scheduler scanning from Agent execution

Introduce separate logical Worker responsibilities:

- Scheduler pass: update heartbeat, scan due tasks, create due/skipped/missed run records, and advance `next_run_at`.
- Executor pass: claim runnable pending runs, enforce same-session slot, run Agent with timeout, and finalize.
- Notification pass: deliver notification outbox independently.

The scheduler pass must not await Agent runtime, LLM routing, MCP discovery, or notification delivery. It can run in the same deployable process as the executor only if the execution happens in a separate loop/thread/process boundary that cannot block the next scan; otherwise deploy separate processes.

Alternative considered: keep a single `run_once()` that creates and executes one run. This preserves simplicity but is the observed source of missed scans when execution is slow, so it does not satisfy the SLA.

### 2. Use durable timing fields and derived delay metrics

Keep `scheduled_for`, `available_at`, `started_at`, and `finished_at` as the source of truth, and derive:

- scheduling delay: `available_at - scheduled_for` or run creation time minus `scheduled_for`;
- executor delay: `started_at - available_at`;
- runtime duration: `finished_at - started_at`;
- stale/timeout reason from terminal error summary.

If additional persisted fields are needed for fast listing, store only derived safe fields. The authoritative audit remains the run table and progress/notification records.

Alternative considered: expose only status and next run. That is insufficient to distinguish scheduler not running, executor backlog, Agent timeout, and notification failure.

### 3. Coalesce missed and overlapping periodic occurrences

For periodic tasks, recovery creates at most one latest missed run per task. Older windows are recorded as skipped/missed either as explicit lightweight run rows or as a safe aggregate on the latest compensation run. For active overlap, the scheduler marks the new interval skipped/coalesced when the previous same-task or same-session run is still active beyond the bounded queue policy.

Alternative considered: enqueue every missed interval. That causes catch-up storms and makes periodic Agent tasks less reliable after downtime.

### 4. Stale running cleanup before compensation

Before creating compensation runs, the scheduler/executor health pass must detect `running` runs whose lease expired and whose owner heartbeat is stale. Those runs become `failed` with a stale reason and release their session slot if still owned by that run. Only after stale cleanup should latest-missed compensation be considered.

Alternative considered: reclaim and execute the same old `running` run. That risks duplicating side effects and hides the fact that the prior Worker died.

### 5. Execution timeout is a scheduled-task boundary

Scheduled Agent executions get a default 10-minute timeout. Timeout requests cancellation via the existing stop/cancel path where possible, then finalizes the run as `failed: timeout` and releases the session slot. This does not alter manual chat or non-scheduled Agent runs.

Alternative considered: rely only on Agent internal budgets. The issue is operational reliability of the schedule, so an outer scheduled-task timeout is needed even if the Agent loop has its own safeguards.

### 6. Worker heartbeat and health are first-class diagnostics

Persist Worker identity, role (`scheduler`, `executor`, `notification`), last heartbeat time, and optional version/config summary. Health is computed as unhealthy after 60 seconds without heartbeat. API/UI should show missing or unhealthy Workers alongside task state.

Deployment should explicitly enable scheduled Workers in local scripts, Docker profile/env, and production release checks. A configured task with no healthy scheduler must show a warning.

Alternative considered: infer health from recent run activity. That fails for low-frequency tasks and cannot distinguish "no work due" from "no worker running".

## Risks / Trade-offs

- [More Worker roles and health state increase operational complexity] → Keep the roles logical and allow a combined process only if scheduler scans remain non-blocking; provide clear health output.
- [Skipping/coalescing occurrences may surprise users expecting every interval] → Surface skipped/missed counts and make the contract explicit: reliability means bounded latest state, not replaying every interval.
- [Timeout can stop legitimate long tasks] → Default to 10 minutes for scheduled tasks and record timeout clearly; future work can add per-task timeout if needed.
- [Heartbeat table or derived metrics add migration work] → Make fields additive and safe to ignore on rollback.
- [LLM/MCP failures still make execution fail] → The scheduler remains accurate; execution failures are recorded with retry/timeout semantics instead of blocking the scan loop.

## Migration Plan

1. Add additive persistence for Worker heartbeat/role and any needed run diagnostic fields; keep existing tasks and runs valid.
2. Refactor Worker entry points so scheduler scan does not synchronously wait for Agent execution.
3. Add stale-run cleanup and latest-missed compensation before enabling the new path by default.
4. Add executor timeout and same-session bounded overlap handling.
5. Add API/UI health and timing diagnostics.
6. Update local and Docker deployment docs/config so scheduled-task Workers are explicit and health-checkable.
7. Rollback: disable the new scheduler/executor mode and Worker heartbeat display; existing task and run records remain readable, and notification isolation remains unchanged.

## Open Questions

None.
