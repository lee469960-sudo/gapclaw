# Plan: reliable-scheduled-task-sla

## Formal Source

`openspec/changes/archive/2026-10-08-reliable-scheduled-task-sla/tasks.md` is the archived formal task source.

This plan does not redefine requirements. It maps the OpenSpec tasks to execution phases and verification checkpoints.

Requirements stay in:

- `openspec/changes/archive/2026-10-08-reliable-scheduled-task-sla/proposal.md`
- `openspec/changes/archive/2026-10-08-reliable-scheduled-task-sla/design.md`
- `openspec/changes/archive/2026-10-08-reliable-scheduled-task-sla/specs/session-scheduled-tasks/spec.md`

## Goal

Implement OpenSpec change `reliable-scheduled-task-sla`: make session scheduled tasks reliable at the scheduling boundary, observable in UI/API, and safe under slow Agent execution, Worker recovery, stale leases, timeout, and notification failure.

## Current Phase

Archived

## Execution phases

1. Audit and observability baseline — complete
   - Maps: OpenSpec tasks 1.1, 1.2.
   - Work: audit current scheduler/executor/notification Worker, API payloads, frontend status, deployment paths, and archived scheduled-task changes; add or document baseline regressions for current gaps.
   - Verify: findings are recorded and baseline tests or documented current gaps cover scheduler blocking, stale running, missing Worker health, and notification isolation.

2. Scheduler SLA and Worker health — complete
   - Maps: OpenSpec tasks 2.1, 2.2, 2.3, 2.4.
   - Work: add Worker heartbeat/role state, non-blocking due-occurrence creation, latest-missed compensation, skipped/missed recording, and concurrency-safe idempotency.
   - Verify: unit tests cover healthy/stale/missing/disabled Workers, 15-second SLA behavior with a blocked executor, one compensation run for downtime, and multi-worker duplicate prevention.

3. Executor isolation, timeout, and stale cleanup — complete
   - Maps: OpenSpec tasks 3.1, 3.2, 3.3, 3.4, 3.5.
   - Work: isolate executor processing from scheduler scanning, preserve same-session serialization, clean stale running runs, enforce 10-minute timeout, bound overlap/backlog, and keep notification failure independent from task success.
   - Verify: tests prove scheduler continues while executor is busy, stale work fails safely, timeout releases slots without changing ordinary chat behavior, overlap is skipped/coalesced visibly, and notification failure does not re-run Agent.

4. API, UI, and deployment diagnostics — complete
   - Maps: OpenSpec tasks 4.1, 4.2, 4.3.
   - Work: expose safe timing/health diagnostics in APIs, render Worker health and delay/timeout/stale/skipped/notification states in UI, and update local/Docker/cloud deployment config and checks.
   - Verify: authorization tests prevent leakage, frontend/static tests cover new and legacy rows, and config tests cover default-safe disabled mode and enabled deployment mode.

5. Regression and verification — complete
   - Maps: OpenSpec tasks 5.1, 5.2, 5.3.
   - Work: run affected backend/frontend/OpenSpec/diff checks and record compatibility, idempotency, and rollback findings.
   - Verify: all affected scheduled-task, Agent runtime context, frontend/build, OpenSpec strict validation, and diff hygiene checks pass before the change is considered complete.

## Completion rule

For each OpenSpec task:

1. update `progress.md` with code and test evidence;
2. confirm code and tests are complete;
3. then mark the corresponding checkbox in the change `tasks.md`.

## 5-Question Reboot Check

| Question | Answer |
|----------|--------|
| Where am I? | Change implementation, verification, spec sync, and archive are complete. |
| Where am I going? | Ready for git review/commit/tag flow if requested. |
| What's the goal? | Reliable scheduled-task SLA: 15-second due-run creation, non-blocking scheduler, stale/timeout cleanup, diagnostics, and deployment health. |
| What have I learned? | See `findings.md`. |
| What have I done? | Implemented and verified all OpenSpec tasks 1.1 through 5.3. |
