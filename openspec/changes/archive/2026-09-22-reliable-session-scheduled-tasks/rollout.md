# Scheduled-task rollout, diagnosis, and rollback

This runbook is for the `scheduled-tasks` Compose profile. The API is never a
Scheduler executor: only the profile's Scheduler Worker may claim and execute
task runs. Keep the legacy Tick executor disabled throughout this rollout.

## Preconditions

- Take a database backup and record the deployed API image digest.
- Confirm normal conversations, an Agent without MCP/Skill bindings, and an
  Agent with a read-only MCP binding still complete normally.
- Start with both flags disabled, which is the Compose default:

  ```sh
  SCHEDULED_TASKS_WORKER_ENABLED=false \
  SCHEDULED_TASK_NOTIFICATIONS_WORKER_ENABLED=false \
  docker compose --profile scheduled-tasks up -d
  ```

- Verify there is no old in-process Tick executor. Do not enable old and new
  execution paths together.

### User-approved local staging-equivalent exercise

For this change's local acceptance, an isolated Docker Compose stack with its
own PostgreSQL volume may stand in for staging. It must use the
`scheduled-tasks` profile, a different project name and non-conflicting host
ports from the normal local development processes. Capture the same shadow,
canary, PostgreSQL and disable/enable evidence below. This exercise validates
the deployment wiring but does not replace the isolated staging rollout before
production enablement.

## Rollout stages

### 1. Shadow mode

Run exactly one Scheduler replica. Shadow mode performs no durable occurrence,
lease, run, or Agent work changes.

```sh
SCHEDULED_TASKS_WORKER_ENABLED=true \
SCHEDULED_TASKS_SHADOW_MODE=true \
SCHEDULED_TASK_NOTIFICATIONS_WORKER_ENABLED=false \
docker compose --profile scheduled-tasks up -d scheduled-task-worker
```

Observe at least one expected schedule window. The Worker log must contain
`scheduled_task_worker_shadow_mode` and must not contain `lease_claimed` or an
Agent execution for that window. Compare the expected task next-run values with
the existing legacy schedule before proceeding.

### 2. One-task canary

Choose one low-risk, read-only task in one private test session. Enable the
Scheduler execution flag and notification Worker only if the task has a known
safe recipient.

```sh
SCHEDULED_TASKS_WORKER_ENABLED=true \
SCHEDULED_TASKS_SHADOW_MODE=false \
SCHEDULED_TASK_NOTIFICATIONS_WORKER_ENABLED=true \
docker compose --profile scheduled-tasks up -d \
  scheduled-task-worker scheduled-task-notification-worker
```

Wait for one scheduled occurrence and one manual run. The session must receive
one real assistant result per run; a notification failure may create a warning,
but must not rerun the Agent.

### 3. Expand gradually

Expand by small task cohorts and observe a full interval/Cron cycle between
cohorts. Keep one Scheduler replica during this change. A second replica is
only introduced after the canary evidence below is retained.

## Dashboard and canary success criteria

Use structured `app.scheduled_tasks` events and the run/outbox tables. Never
put prompts, model output, credentials, or channel tokens in dashboards.

| Signal | Healthy canary | Stop / investigate when |
| --- | --- | --- |
| Occurrence uniqueness | one run for each `(task_id, occurrence_key)` | duplicate run/occurrence is observed |
| Lease ownership | one current owner; expired lease later recovered once | two live workers report competing ownership |
| Session serialization | at most one active slot per `session_id` | concurrent Agent runs share one session |
| Queue delay | pending work starts within the expected poll interval when no session is busy | delay grows or exceeds one-hour terminal boundary |
| Writeback | every `succeeded` run has a non-empty `chat_message_id` in its source session | `succeeded` has no result message or user sees a blank result |
| Notifications | delivery succeeds, or failure is isolated in the outbox | a notification retry creates another Agent run |
| Compatibility | ordinary chat and MCP/Skill sessions remain normal | routes, caches, MCP retry behavior, or resume behavior regress |

PostgreSQL read-only checks (replace no IDs; these contain no message content):

```sql
-- Duplicate occurrence must return no rows.
SELECT task_id, occurrence_key, COUNT(*)
FROM scheduled_task_runs
GROUP BY task_id, occurrence_key
HAVING COUNT(*) > 1;

-- Succeeded work must have a bound message.
SELECT id, task_id
FROM scheduled_task_runs
WHERE state = 'succeeded' AND chat_message_id IS NULL;

-- Only one non-expired active slot per session is possible by schema; inspect
-- active owners during the canary.
SELECT session_id, active_run_id, lease_owner, lease_expires_at
FROM scheduled_task_session_slots
WHERE active_run_id <> '';

-- Notification retries are separate from Agent execution.
SELECT state, COUNT(*)
FROM scheduled_task_notification_deliveries
GROUP BY state;
```

## On-call diagnosis

1. Start with the task/run history and the session result. Record the run ID,
   occurrence key, state, attempt, timestamps, lease owner/expiry, and
   `chat_message_id`; do not copy prompt/output into the incident.
2. For `pending`, check the session slot and Worker health. A queued run over
   one hour becomes terminal with `scheduled_task_queue_age_exceeded`.
3. For `running` with an expired lease, restore one Scheduler Worker. The next
   scan may reclaim the same run; do not create a manual replacement first.
4. For `succeeded` without a visible result, inspect the bound message by ID;
   this is a writeback defect and must not be papered over with a notification.
5. For notification failure, inspect only the delivery row and channel mapping.
   Retry delivery through its outbox path; never rerun the Agent to resend.
6. For suspected duplicate external MCP effects, stop further execution and
   preserve run/lease evidence. The occurrence key prevents duplicate claims,
   but non-idempotent external side effects require application-specific review.

## No-overlap rollback

1. Disable Scheduler execution first; do not re-enable a legacy executor:

   ```sh
   SCHEDULED_TASKS_WORKER_ENABLED=false \
   SCHEDULED_TASK_NOTIFICATIONS_WORKER_ENABLED=false \
   docker compose --profile scheduled-tasks up -d
   docker compose --profile scheduled-tasks stop \
     scheduled-task-worker scheduled-task-notification-worker
   ```

2. Confirm both Worker containers have stopped and there are no non-expired
   leases or active session slots. Let an already-running Agent finish or
   expire/recover under the same single-executor procedure; never start a
   second executor to force completion.
3. Preserve `scheduled_tasks`, runs, progress snapshots, and notification
   deliveries for audit. Code rollback may leave these additive tables in place.
4. Record the reason, deployed digest, Worker logs, and non-sensitive run IDs.
   Re-enable only one Scheduler after the diagnosis and a fresh shadow pass.

## Staging evidence still required

Task 6.3 is not complete until a staging operator records: shadow-mode log
evidence, one scheduled plus one manual canary, the four read-only checks above
returning healthy results, a disable/enable exercise, and proof that no duplicate
occurrence or concurrent session execution was created. Local SQLite evidence
does not replace this staging exercise.
