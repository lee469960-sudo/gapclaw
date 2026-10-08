## Why

Session scheduled tasks currently can appear late or idle because the same Worker loop both creates due runs and synchronously executes Agent runs. Long Agent executions, LLM/MCP network retries, stale running leases, disabled deployment profiles, or notification failures can make a configured task look inaccurate or not running even when its schedule is valid.

This change makes scheduled tasks reliable at the scheduling boundary: occurrences must be created on time, executor delays must be visible, stale work must be resolved safely, and Worker health must be observable before users rely on periodic Agent automation.

## What Changes

- Define a scheduling SLA: enabled due tasks must create a durable run within 15 seconds of the scheduled time while a healthy scheduler Worker is active.
- Split scheduling from execution so creating due runs is not blocked by long Agent execution, LLM/MCP retries, or notification delivery.
- Keep same-session execution serialized, but skip or coalesce overlapping periodic occurrences instead of building an unbounded backlog.
- On Worker recovery, create at most the latest missed occurrence per task and record older missed occurrences as skipped/missed.
- Add a default 10-minute execution timeout for scheduled Agent runs; timeout ends the run as `failed` with a timeout reason.
- Treat stale expired `running` leases as failed/stale, then apply the latest-missed compensation policy rather than blindly re-executing old work.
- Keep notification delivery independent from task execution success: failed IM delivery does not change a successful task run.
- Add Worker heartbeat and health status. A Worker missing heartbeat for 60 seconds is unhealthy.
- Extend task and run views with timing diagnostics: `next_run`, `last_scheduled`, `last_started`, `last_finished`, `last_status`, scheduling delay, and Worker health.
- Make Docker/cloud deployment of scheduled-task Workers explicit and verifiable so a configured task cannot silently lack a running scheduler/executor.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `session-scheduled-tasks`: scheduling SLA, scheduler/executor separation, overlap handling, missed-run compensation, stale lease handling, execution timeout, Worker health, deployment readiness, and UI/API timing diagnostics.

## Impact

- Backend scheduled-task services: occurrence creation, claim/execution loop, session-slot handling, stale lease recovery, timeout/cancellation paths, retry classification, notification isolation, and observability.
- Worker processes and deployment: scheduled-task Worker topology, environment/profile defaults, health/heartbeat reporting, logs, and operational checks.
- API/UI: task list and run history response fields, session scheduled-task status display, Worker health warnings, delay diagnostics, and missed/skipped visibility.
- Tests: scheduler SLA, missed occurrence coalescing, stale running cleanup, timeout finalization, worker heartbeat health, notification isolation, same-session serialization, and deployment/profile configuration regression coverage.
