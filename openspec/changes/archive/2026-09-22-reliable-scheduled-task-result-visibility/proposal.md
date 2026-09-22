## Why

Repeated session scheduled tasks can appear to finish without showing the final conversation result, and repeated runs in the same session increasingly hit the runtime's no-progress stop because each occurrence reuses an ever-growing, repetitive conversation history. Operators need scheduled runs to have visible terminal results, clear failure semantics, and bounded execution context without breaking normal conversation behavior.

## What Changes

- Make every scheduled-task occurrence produce a durable, user-visible terminal result in the originating conversation, including success, failure, cancellation, no-progress auto-stop, notification outcome, and the bound assistant message or safe quality summary when one exists.
- Ensure the task list and run history expose the final result content or a safe preview, not only a `chat_message_id`.
- Keep notification delivery independent from task success: a successful conversation writeback with failed IM delivery remains a successful run with a visible notification warning.
- Bound scheduled-task runtime context so repeated occurrences in the same conversation do not accumulate unlimited prior scheduled-task transcripts and do not become more likely to no-progress stop solely because of earlier repeats.
- Preserve the ordinary conversation path and Agent/Skill/MCP bindings while applying the context-bound behavior only to scheduled-task executions.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `session-scheduled-tasks`: scheduled-task result visibility, terminal-state semantics, notification/result separation, and repeated-run context control.

## Impact

- Affected backend: scheduled-task runtime finalization, progress/run APIs, conversation message metadata, notification warning writeback, and Agent runtime context construction for `source=scheduled_task`.
- Affected frontend: session scheduled progress card, scheduled-task list/history, conversation history refresh/reconnect behavior, and terminal result display.
- Affected tests: scheduled-task worker/runtime tests, authorization/history API tests, notification isolation tests, Agent runtime context/no-progress regression tests, and frontend rendering tests for scheduled terminal results.
- No database migration is expected unless implementation chooses to persist additional derived fields; existing `ScheduledTaskRun.chat_message_id`, progress, and notification records should remain the authoritative base records.
