## 1. Backend result model and APIs

- [x] 1.1 Add a scheduled-run terminal result serializer derived from `ScheduledTaskRun`, bound `ChatMessage`, `ScheduledTaskProgress`, and notification deliveries; verify API tests cover success, failed, cancelled, no-progress, and notification-failed payloads with safe content previews.
- [x] 1.2 Extend `scheduled_task_progress` and `list_scheduled_task_runs` responses to return terminal result content/preview, step tail, notification summaries, and bound message metadata; verify authorization tests prove unrelated users cannot read result content or previews.
- [x] 1.3 Ensure notification delivery failure after successful conversation writeback leaves the run `succeeded` while surfacing a warning in the terminal payload; verify notification-worker tests prove IM failure does not rerun the Agent or overwrite the result.

## 2. Scheduled runtime completion semantics

- [x] 2.1 Detect runtime no-progress/budget forced-stop replies for scheduled executions and finalize them as non-success terminal outcomes with safe last-step/last-output summaries; verify worker tests do not mark no-progress fallback text as `succeeded`.
- [x] 2.2 Preserve success only when a non-empty assistant message with the scheduled run id is durably bound to the original session; verify empty output, missing writeback, cancellation, and successful writeback cases retain existing fail-closed behavior.
- [x] 2.3 Keep scheduled task retries limited to infrastructure failures and prevent business/no-progress/notification failures from automatic Agent reruns; verify lifecycle tests cover retry classification and attempt counts.

## 3. Bounded scheduled-task context

- [x] 3.1 Add scheduled-task-specific context shaping gated by `message_meta.source=scheduled_task`, excluding repeated prior scheduled trigger/result/warning transcripts while preserving current trigger, Agent/session identity, model, Skill/MCP bindings, and needed recent conversation context; verify runtime context tests inspect the assembled context.
- [x] 3.2 Add compact recent scheduled-outcome summaries or references for audit without inlining all prior runs; verify repeated-run tests show context size stays bounded across many occurrences in the same session.
- [x] 3.3 Verify ordinary manual conversations and non-scheduled IM/channel executions are unchanged, and that Agents without required Skill/MCP bindings produce clear missing-capability results rather than fabricated success.

## 4. Frontend result visibility

- [x] 4.1 Render scheduled-task running progress in the originating conversation timeline and terminal outcomes as ordinary assistant conversation replies or safe quality summaries for success, failed, cancelled, no-progress, queue timeout, and notification-warning states; verify frontend tests cover refresh/reconnect after a run leaves `running`.
- [x] 4.2 Update the scheduled-task list/history UI to show final result preview, notification state, failure reason, and a locate/expand affordance for the bound message; verify UI tests cover a successful result where only `chat_message_id` was previously visible.
- [x] 4.3 Ensure running progress cards transition to ordinary conversation terminal replies or safe quality summaries without disappearing when polling misses the exact state transition or the tab was hidden; verify component tests cover missed-poll and page-refresh recovery.

## 5. Integration and regression coverage

- [x] 5.1 Add backend integration tests for repeated interval executions in the same session, proving later runs still call required tools or produce explicit missing-capability/non-success results rather than failing solely from accumulated repeated history.
- [x] 5.2 Add regression tests for scheduled-task result visibility with notification failure, no-progress auto-stop, empty output, cancellation, manual retry, and run-history authorization.
- [x] 5.3 Run affected backend scheduled-task/runtime/channel suites and affected frontend tests; verify ordinary Agent conversations, MCP connection/retry/cache behavior, configured message channels, and scheduled-task stop/retry behavior remain compatible.
- [x] 5.4 Run strict OpenSpec validation and diff checks; record compatibility, idempotency, gray-release/rollback notes, and known limitations before marking the change complete.
