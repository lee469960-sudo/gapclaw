## Context

See `proposal.md` for motivation. The current scheduled-task worker calls the normal `run_agent` path with `message_meta.source=scheduled_task`, captures live steps into `ScheduledTaskProgress`, then marks success only after it can bind an assistant `ChatMessage` whose metadata contains the scheduled run id. The frontend polls one running scheduled task and displays an in-conversation progress card, while the task dialog exposes recent run state and a `chat_message_id`.

The current approach correctly reuses the session runtime and authorization model, but it has two visibility/liveness gaps:

- the UI can miss the transition from running to terminal and leave users without the final result content;
- repeated scheduled occurrences in the same session add highly similar user/result/warning messages to the normal conversation history, increasing the chance that the runtime sees repeated outputs and stops as no-progress.

## Goals / Non-Goals

**Goals:**

- Make scheduled-task terminal results durable and visible in the originating conversation after refresh/reconnect.
- Keep scheduled-task success tied to successful conversation writeback, while showing notification failures as warnings rather than task failure.
- Bound model context for scheduled executions so repeated periodic runs do not inject unlimited prior scheduled-task transcripts.
- Preserve current Agent, model, Skill, MCP, channel authorization, and ordinary manual conversation behavior.

**Non-Goals:**

- Do not remove the no-progress safety stop or classify repeated no-progress as success.
- Do not introduce a separate execution engine for scheduled tasks.
- Do not broaden scheduled-task authorization or expose results outside the originating session.
- Do not add deletion of individual output results in this change.

## Decisions

### 1. Represent scheduled terminal results as first-class conversation UI records

The backend should keep `ScheduledTaskRun` as the authoritative execution ledger and `ChatMessage` as the authoritative conversation output. The API should derive a safe terminal result payload from those records: run id, task id, state, source, timestamps, attempt, `chat_message_id`, content preview or content when authorized, step tail, error summary, and notification summaries.

The conversation UI should render this terminal payload as a scheduled-task result card in the original timeline. If a bound assistant message exists, the card should either anchor to that message or include the safe rendered content so users do not need to manually search for `chat_message_id`.

Alternative considered: only refresh the existing message list more aggressively. Rejected because it does not solve run-history visibility, notification warnings, or missed terminal transitions after reconnect.

### 2. Separate run completion from notification delivery

Worker finalization remains:

1. run Agent;
2. persist/bind assistant message;
3. mark run `succeeded`;
4. enqueue/send notification.

If step 4 fails after retries, the run remains `succeeded` and the UI shows a notification warning. Notification failure must never rerun the Agent and must never overwrite the terminal result.

Alternative considered: mark the whole scheduled run failed when notification fails. Rejected because the user-visible conversation result is the primary scheduled-task deliverable and may already be correct.

### 3. Use a scheduled-task context window for repeated occurrences

Scheduled executions should still use the formal session Agent runtime, but context assembly should recognize `message_meta.source=scheduled_task`. In that mode it should bound previous scheduled-task messages and warnings before they enter the model context.

The bounded context should include:

- the current trigger message;
- the originating Agent/session identity;
- current or snapshotted model/Skill/MCP configuration;
- ordinary recent user conversation context needed for continuity;
- a compact summary of recent scheduled-task outcomes, not full repeated transcripts;
- references to prior bound messages if needed for audit, without inlining every prior run.

This should be a context-shaping change, not an authorization bypass. Tool availability, MCP connection behavior, cache behavior, and bound Skills remain exactly those of the selected Agent/session.

Alternative considered: run each scheduled occurrence in a brand-new hidden session. Rejected because it breaks the requirement that results return to the originating session and would surprise users who expect session-bound Agent configuration.

### 4. Treat no-progress auto-stop as a visible non-success terminal outcome

The runtime's no-progress protection should remain active. Scheduled finalization should detect the engine-owned no-progress/budget-stop fallback and record the run as failed or another existing non-success state with a clear safe summary. The UI should show the last valid steps and any intermediate tool result previews, but must not label this as task completion.

Alternative considered: accept the last fallback text as a successful scheduled result. Rejected because it hides the difference between a real holdings answer and a protective stop.

## Risks / Trade-offs

- [Context pruning can hide useful prior details] → Include a compact recent scheduled-outcome summary and keep full prior results available through run history and bound messages.
- [Terminal result UI duplicates assistant messages] → Keep progress cards limited to running executions and render terminal outcomes as ordinary assistant messages or safe quality summaries, de-duplicated by `scheduled_task_run_id` and `chat_message_id`.
- [No-progress detection may still happen for genuinely unavailable tools] → Preserve the non-success result with actionable missing-capability or failure summary rather than forcing success.
- [Notification warning messages can themselves pollute context] → Exclude scheduled notification warning boilerplate from scheduled-task model context while keeping it visible to users.

## Migration Plan

1. Add backend serialization for scheduled terminal results without changing existing run/message tables.
2. Add context-shaping support gated by `message_meta.source=scheduled_task`.
3. Update frontend polling/history to render running and terminal scheduled cards from the same run payload.
4. Add regression coverage for repeated scheduled runs, notification failure isolation, no-progress non-success display, and refresh/reconnect recovery.
5. Rollback by disabling the new scheduled terminal card rendering and context-shaping gate; existing run ledgers and bound messages remain valid.
