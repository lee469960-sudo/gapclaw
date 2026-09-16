## 1. Batch protocol and parser

- [x] 1.1 Locate the current ReAct action parser/dispatcher and add a batch action schema for `parallel`, `sequence` and `transaction`; verify unit tests accept valid batch envelopes and reject missing mode, duplicate child ids and malformed child actions.
- [x] 1.2 Preserve legacy single tool calls alongside the new batch path; verify existing single-call ReAct runtime tests still pass without changing their expected results.
- [x] 1.3 Apply existing authorization, sandbox, MCP routing, file path and redaction checks to every batch child before execution; verify unauthorized children are rejected by the same rule as equivalent standalone calls.

## 2. Batched execution engine

- [x] 2.1 Implement `parallel` batch execution for independent read-only/diagnostic children with compact aggregate results; verify mixed success/failure children produce per-child status without extra LLM turns.
- [x] 2.2 Implement `sequence` batch execution with ordered child execution and dependency failure skipping; verify a failed prerequisite marks dependent children skipped and does not execute them.
- [x] 2.3 Implement bounded and visible retry/fallback policy for transient child failures; verify validation failures do not auto-retry and any allowed fallback is recorded with attempt counts.

## 3. Batch file read behavior

- [x] 3.1 Add batch-safe file read/range support that returns `complete: true` for small complete files and `complete: false` with omitted range metadata for oversized files; verify tests cover small, medium and oversized files.
- [x] 3.2 Coalesce adjacent ranges from the same file at the physical read layer while preserving child-level requested ranges in results; verify a test shows one physical read can satisfy multiple child range results.
- [x] 3.3 Add model-facing result compaction for batch reads with materialized detail references where needed; verify large read batches do not inline excessive repeated content and remain inspectable.

## 4. Transactional patch writes

- [x] 4.1 Implement patch-only `transaction` batch dry-run validation across all child patches; verify every target file remains unchanged when any child patch conflicts or targets a disallowed path.
- [x] 4.2 Commit all transaction patches only after every dry-run validation succeeds; verify tests cover multi-file success and confirm all expected diffs are applied.
- [x] 4.3 Reject unrestricted full-file overwrite requests in batch transactions by default; verify the rejection prevents sibling writes in the same transaction.

## 5. Observability and UI

- [x] 5.1 Extend execution event/result models to record a batch parent plus expandable child actions, statuses, timing, redacted arguments and detail references; verify serialization tests cover success, failure and skipped children.
- [x] 5.2 Update the conversation/execution UI to render batch tool calls collapsed by default with an expand control for child details; verify frontend build and source/component tests cover expandable batch display.
- [x] 5.3 Ensure batch summaries appear in LLM context while full child details remain available to users/debugging; verify tests assert compact model-facing content and inspectable stored details.

## 6. Prompting, safeguards and integration verification

- [x] 6.1 Update ReAct runtime instructions/coaching so models prefer batch read/patch for suitable same-domain work and avoid batching dependent or unsafe actions; verify prompt contract tests include batch guidance.
- [x] 6.2 Add integration tests covering one LLM turn producing multiple batch child tool results and the next turn receiving a compact summary; verify no repeated reasoning turn is inserted between child actions.
- [x] 6.3 Add regression tests proving batching cannot load unselected MCPs, cross security domains, expose secrets, or bypass existing file/sandbox permissions; verify the relevant agent-runtime/MCP routing tests pass.
- [x] 6.4 Run the affected backend and frontend suites plus `openspec validate react-engine-batched-tool-execution --strict` and `git diff --check`; record results before marking the change complete.
