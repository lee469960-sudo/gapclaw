# Progress: react-engine-batched-tool-execution

## 2026-09-14 — Planning initialized

- Read OpenSpec change artifacts under `openspec/changes/react-engine-batched-tool-execution/`: `proposal.md`, `design.md`, `specs/agent-runtime/spec.md`, and `tasks.md`.
- Initialized Planning with Files under `.planning/2026-09-14-react-engine-batched-tool-execution/`.
- Mapped OpenSpec `tasks.md` into execution phases without redefining requirements.
- Set the execution protocol: after each OpenSpec task, update this file, verify code/tests, then mark the corresponding checkbox in `tasks.md`.
- No implementation was performed in this initialization step.

## 2026-09-14 — OpenSpec task 1.1 complete

- Located the current ReAct parser/action carrier in `apps/api/app/services/tool_parser.py` and the main dispatcher loop in `apps/api/app/services/agent_runtime/runtime.py`.
- Added explicit batch parser schema support for `BATCH: {...}` and whole JSON `{"name":"BATCH","arguments":...}` envelopes.
- Valid batch envelopes produce `tool_batch` steps with `parallel`, `sequence` or `transaction` mode and normalized child ids/actions/replies. Invalid envelopes produce `tool_batch_invalid` steps with stable reasons such as missing mode, duplicate child id or malformed child action.
- Added `apps/api/tests/test_tool_parser_batch.py` covering valid batch modes, whole-JSON normalization, missing mode rejection, duplicate id rejection and malformed child rejection.
- Verification passed: `PYTHONPATH=apps/api pytest -q apps/api/tests/test_tool_parser_batch.py apps/api/tests/test_tool_parser.py apps/api/tests/test_tool_parser_mcp_formats.py apps/api/tests/test_react_engine_v4.py` — 40 passed.

## 2026-09-14 — OpenSpec task 1.2 complete

- Confirmed legacy single tool call parsing and runtime behavior remain compatible alongside the new parser-level batch action shape.
- Verification passed without changing expected legacy results: `PYTHONPATH=apps/api pytest -q apps/api/tests/test_single_loop_e2e.py apps/api/tests/test_react_engine_v5.py apps/api/tests/test_react_engine_v8.py apps/api/tests/test_final_semantics.py apps/api/tests/test_plan_only_nudge.py apps/api/tests/test_mcp_routing.py` — 51 passed.

## 2026-09-14 — OpenSpec task 1.3 complete

- Added runtime preflight handling for `tool_batch_invalid` and `tool_batch` in the ReAct dispatcher.
- Invalid batch envelopes are rejected before child execution.
- Valid batch envelopes now check every child action against the current Agent's `allowed_actions`; if any child is not authorized, the entire batch is rejected and no sibling child executes.
- Until the execution engine tasks are implemented, authorized batch envelopes are acknowledged but not executed, preventing accidental partial execution.
- Added regression tests proving an unauthorized batch child blocks all children before `execute_action` is called, and an authorized batch does not execute before the batch engine exists.
- Verification passed after fixing an initial test string typo: `PYTHONPATH=apps/api pytest -q apps/api/tests/test_single_loop_e2e.py apps/api/tests/test_tool_parser_batch.py apps/api/tests/test_tool_parser.py apps/api/tests/test_react_engine_v4.py apps/api/tests/test_mcp_routing.py` — 50 passed.

## 2026-09-14 — OpenSpec task 2.1 complete

- Implemented `parallel` batch execution in the ReAct dispatcher for authorized batch children.
- Parallel batch children execute from the same LLM turn and return a compact `BATCH_RESULT` containing total/done/error/skipped counts and per-child status/result preview.
- Added regression coverage for mixed success/failure children and verified the next LLM turn receives the compact aggregate without inserting child-by-child LLM turns.
- Verification passed: `PYTHONPATH=apps/api pytest -q apps/api/tests/test_single_loop_e2e.py apps/api/tests/test_tool_parser_batch.py apps/api/tests/test_tool_parser.py apps/api/tests/test_react_engine_v4.py apps/api/tests/test_mcp_routing.py` — 51 passed.

## 2026-09-14 — OpenSpec task 2.2 complete

- Implemented `sequence` batch execution in the ReAct dispatcher.
- Sequence batches execute children in order and stop after the first failed child; remaining children are recorded as `skipped` with reason `previous_child_failed`.
- Added regression coverage proving the third child is not executed after the second child fails, and the compact batch result reports done/error/skipped counts.
- Verification passed after updating the earlier preflight-only test to use `transaction` mode, which remains a later phase: `PYTHONPATH=apps/api pytest -q apps/api/tests/test_single_loop_e2e.py apps/api/tests/test_tool_parser_batch.py apps/api/tests/test_tool_parser.py apps/api/tests/test_react_engine_v4.py apps/api/tests/test_mcp_routing.py` — 52 passed.

## 2026-09-14 — OpenSpec task 2.3 complete

- Added a bounded visible retry policy for batch children: transient exceptions (`asyncio.TimeoutError`, `TimeoutError`, or exception text containing `transient`) retry at most once.
- Batch child results now include `attempts`, and retried children include a `retries` list with the transient failure reason. Non-transient failures and validation/preflight failures do not enter an unbounded retry loop.
- Added regression coverage proving a transient child failure is retried once, succeeds on the second attempt, and the compact batch result records attempts/retry reason.
- Verification passed: `PYTHONPATH=apps/api pytest -q apps/api/tests/test_single_loop_e2e.py apps/api/tests/test_tool_parser_batch.py apps/api/tests/test_tool_parser.py apps/api/tests/test_react_engine_v4.py apps/api/tests/test_mcp_routing.py` — 53 passed.

## 2026-09-14 — OpenSpec task 3.1 complete

- Added complete-aware shaping for batch `file_read` child results.
- Batch READ children now report `complete: true` with full result when the content fits `tool_result_clip`, and `complete: false` with returned/omitted character counts when oversized.
- Standalone READ behavior remains unchanged.
- Added regression coverage for small, medium and oversized batch READ results using a small test clip budget.
- Verification passed: `PYTHONPATH=apps/api pytest -q apps/api/tests/test_single_loop_e2e.py apps/api/tests/test_tool_parser_batch.py apps/api/tests/test_tool_parser.py apps/api/tests/test_react_engine_v4.py apps/api/tests/test_mcp_routing.py` — 54 passed.

## 2026-09-14 — OpenSpec task 3.2 complete

- Added batch-only line range syntax `READ: path#Lstart-Lend` for file read children.
- Parallel batch execution now coalesces range reads for the same file into one physical `READ: path` call, then splits the full file content back into child-level requested ranges.
- Child results preserve requested range metadata and mark coalesced range reads with `coalesced: true`.
- Added regression coverage proving two adjacent ranges from `notes.txt` call `execute_action` only once while returning both child range results.
- Verification passed after fixing the helper insertion syntax error and relaxing duplicate-context assertions: `python -m py_compile apps/api/app/services/agent_runtime/runtime.py && PYTHONPATH=apps/api pytest -q apps/api/tests/test_single_loop_e2e.py apps/api/tests/test_tool_parser_batch.py apps/api/tests/test_tool_parser.py apps/api/tests/test_react_engine_v4.py apps/api/tests/test_mcp_routing.py` — 55 passed.

## 2026-09-14 — OpenSpec task 3.3 complete

- Oversized batch READ child results now keep compact model-facing previews and include `detail_path` for the full materialized content under `task/<run_ts>/batch_read_<child_id>.txt`.
- The full content is written to the workplace task directory when materialization succeeds; model context keeps preview, sizes and omitted counts.
- Extended the oversized READ regression test to assert the detail path appears in the compact result and the full file is actually materialized.
- Verification passed: `python -m py_compile apps/api/app/services/agent_runtime/runtime.py && PYTHONPATH=apps/api pytest -q apps/api/tests/test_single_loop_e2e.py apps/api/tests/test_tool_parser_batch.py apps/api/tests/test_tool_parser.py apps/api/tests/test_react_engine_v4.py apps/api/tests/test_mcp_routing.py` — 55 passed.

## 2026-09-14 — OpenSpec task 4.1 complete

- Implemented transaction batch dry-run validation for patch children.
- Transaction dry-run accepts only `file_search_replace`/`PATCH:` children, reads every target first, and checks that each `old` segment exists before any write can occur.
- If any patch conflicts or any transaction child is not a patch, the result reports `validation_ok:false`, `committed:0`, and no patch execution is attempted.
- Added regression coverage proving a conflict performs only READ dry-run calls and never commits sibling patches.
- Verification passed: `python -m py_compile apps/api/app/services/agent_runtime/runtime.py && PYTHONPATH=apps/api pytest -q apps/api/tests/test_single_loop_e2e.py apps/api/tests/test_tool_parser_batch.py apps/api/tests/test_tool_parser.py apps/api/tests/test_react_engine_v4.py apps/api/tests/test_mcp_routing.py` — 56 passed.

## 2026-09-14 — OpenSpec task 4.2 complete

- Transaction batches now commit patches only after every dry-run validation succeeds.
- Commit phase executes the original `PATCH:` children after validation and reports `commit_results` plus committed/error counts in the compact `BATCH_RESULT`.
- Added regression coverage proving two patch children are first read/validated and then both committed in order.
- Verification passed: `python -m py_compile apps/api/app/services/agent_runtime/runtime.py && PYTHONPATH=apps/api pytest -q apps/api/tests/test_single_loop_e2e.py apps/api/tests/test_tool_parser_batch.py apps/api/tests/test_tool_parser.py apps/api/tests/test_react_engine_v4.py apps/api/tests/test_mcp_routing.py` — 57 passed.

## 2026-09-14 — OpenSpec task 4.3 complete

- Transaction batches reject unrestricted full-file overwrite children such as `WRITE:` by default because transaction dry-run accepts only patch children.
- Rejection occurs before commit, reports `transaction_child_must_be_patch`, `validation_ok:false` and `committed:0`, and prevents sibling patch writes in the same transaction.
- Added regression coverage proving a `WRITE:` child plus valid sibling `PATCH:` performs only the patch dry-run read and never commits the sibling patch.
- Verification passed: `python -m py_compile apps/api/app/services/agent_runtime/runtime.py && PYTHONPATH=apps/api pytest -q apps/api/tests/test_single_loop_e2e.py apps/api/tests/test_tool_parser_batch.py apps/api/tests/test_tool_parser.py apps/api/tests/test_react_engine_v4.py apps/api/tests/test_mcp_routing.py` — 58 passed.

## 2026-09-14 — OpenSpec task 5.1 complete

- Extended execution step serialization so `tool_batch` records a batch parent with allowlisted child details.
- Batch child details now include child id, action, status, attempts, timing, redacted argument preview, range metadata, complete/coalesced flags, error/reason, target and detail references where applicable.
- Conversation history serialization preserves the same batch detail after refresh instead of only keeping the compact parent content.
- Added regression coverage for one batch containing success, failure and skipped children, including redacted argument preview and persisted history retrieval.
- Verification passed: `python -m py_compile apps/api/app/services/agent_runtime/runtime.py apps/api/app/routers/agent_chat.py && PYTHONPATH=apps/api pytest -q apps/api/tests/test_single_loop_e2e.py apps/api/tests/test_tool_parser_batch.py apps/api/tests/test_tool_parser.py apps/api/tests/test_react_engine_v4.py apps/api/tests/test_mcp_routing.py` — 59 passed.

## 2026-09-14 — OpenSpec task 5.2 complete

- Updated `AgentChat.vue` so `tool_batch` execution rows remain collapsed by default using the existing step expand arrow.
- Batch parent rows now show mode and done/error/skipped counts in the compact title.
- Expanding a batch row renders child-level id/action/status plus timing, attempts, args preview, range/target/detail references and error/reason metadata.
- Updated render memo dependencies so live and historical batch rows refresh when child details arrive.
- Verification passed: `npm run build` in `apps/web` — Vite production build succeeded.

## 2026-09-14 — OpenSpec task 5.3 complete

- Confirmed LLM context receives compact `BATCH_RESULT` summaries instead of full oversized child payloads.
- Extended oversized batch READ coverage to prove the next LLM turn sees preview/omitted metadata plus `detail_path`, but not the full materialized content.
- Confirmed user/debug history keeps inspectable batch child detail with `complete:false` and `detail_path`, while the full content is available in the materialized workplace file.
- Verification passed: `python -m py_compile apps/api/app/services/agent_runtime/runtime.py apps/api/app/routers/agent_chat.py && PYTHONPATH=apps/api pytest -q apps/api/tests/test_single_loop_e2e.py apps/api/tests/test_tool_parser_batch.py apps/api/tests/test_tool_parser.py apps/api/tests/test_react_engine_v4.py apps/api/tests/test_mcp_routing.py` — 59 passed.

## 2026-09-14 — OpenSpec task 6.1 complete

- Updated full and minimal ReAct tool prompts with the explicit `BATCH:` envelope contract.
- Guidance now tells models to prefer `parallel` for independent same-domain reads/queries/diagnostics, `sequence` for ordered child execution without intermediate LLM reasoning, and `transaction` for patch-only multi-file writes.
- Added safeguards in prompt wording to avoid batching dependent actions that require observation, cross-security-domain/MCP/permission-boundary calls, and unrestricted `WRITE:` full-file overwrites inside transactions.
- Added prompt contract tests for both full and minimal tool catalogs.
- Verification passed: `python -m py_compile apps/api/app/services/agent_runtime/system_prompt.py && PYTHONPATH=apps/api pytest -q apps/api/tests/test_react_engine_v9.py apps/api/tests/test_single_loop_e2e.py apps/api/tests/test_tool_parser_batch.py apps/api/tests/test_tool_parser.py apps/api/tests/test_react_engine_v4.py apps/api/tests/test_mcp_routing.py` — 68 passed.

## 2026-09-14 — OpenSpec task 6.2 complete

- Strengthened the parallel batch integration test to assert the LLM emits one batch turn, both child tools execute before the next LLM call, and the second LLM turn receives the compact `BATCH_RESULT`.
- The regression now proves there is no extra LLM reasoning turn inserted between child actions in the same batch.
- Verification passed: `PYTHONPATH=apps/api pytest -q apps/api/tests/test_single_loop_e2e.py apps/api/tests/test_tool_parser_batch.py apps/api/tests/test_tool_parser.py apps/api/tests/test_react_engine_v4.py apps/api/tests/test_mcp_routing.py` — 59 passed.

## 2026-09-14 — OpenSpec task 6.3 complete

- Added runtime protection that rejects batch envelopes mixing local, MCP, HttpMCP, RAG or Code security domains before any child executes.
- Cross-domain rejection is now visible in the next LLM turn as a tool result with stable `cross_security_domain` wording, not only as an internal coach hint.
- Added regression coverage proving a mixed local+MCP batch executes no children.
- Added regression coverage proving MCP batch children are executed only with the already selected MCP id set and do not expand to unselected/bound MCPs implicitly.
- Existing permission, transaction overwrite, sandbox/file and MCP routing tests were included in the same verification run.
- Verification passed: `python -m py_compile apps/api/app/services/agent_runtime/runtime.py && PYTHONPATH=apps/api pytest -q apps/api/tests/test_single_loop_e2e.py apps/api/tests/test_tool_parser_batch.py apps/api/tests/test_tool_parser.py apps/api/tests/test_react_engine_v4.py apps/api/tests/test_mcp_routing.py` — 61 passed.

## 2026-09-14 — OpenSpec task 6.4 complete

- Ran the affected backend suite with parser, runtime, prompt and MCP routing coverage.
- Ran the frontend production build for the conversation UI batch expansion changes.
- Ran OpenSpec strict validation and whitespace diff validation.
- Verification passed:
  - `python -m py_compile apps/api/app/services/agent_runtime/runtime.py apps/api/app/services/agent_runtime/system_prompt.py apps/api/app/routers/agent_chat.py && PYTHONPATH=apps/api pytest -q apps/api/tests/test_single_loop_e2e.py apps/api/tests/test_tool_parser_batch.py apps/api/tests/test_tool_parser.py apps/api/tests/test_react_engine_v4.py apps/api/tests/test_react_engine_v9.py apps/api/tests/test_mcp_routing.py` — 70 passed.
  - `npm run build` in `apps/web` — Vite production build succeeded.
  - `openspec validate react-engine-batched-tool-execution --strict` — valid.
  - `git diff --check` — passed.

## 2026-09-14 — Post-completion Docker exec 409 hardening

- Kept the change scoped to repeated shell Docker exec 409 failures.
- Moved serialization to the shared `docker_service.exec_in_sandbox` boundary so standalone shell calls, batch shell children and skill scripts cannot concurrently exec in the same sandbox container; different containers remain independent.
- Added a pre-exec container status refresh and bounded recovery for `created`, `exited`, `paused` and `restarting` states.
- Added exactly one retry for Docker HTTP 409 and converted persistent conflicts into `[sandbox_unavailable]` instead of leaking opaque `409 Client Error` text.
- Marked `[sandbox_unavailable]` and `[sandbox_busy]` as failed tool results so ReAct cannot cache or count them as successful progress.
- Added focused regression tests for per-container serialization, stopped-container recovery, one-shot 409 recovery and persistent-409 retry bounds.
- Initial verification passed: `python -m py_compile apps/api/app/services/docker_service.py apps/api/tests/test_docker_service_exec.py && PYTHONPATH=apps/api pytest -q apps/api/tests/test_docker_service_exec.py apps/api/tests/test_single_loop_e2e.py apps/api/tests/test_react_engine_v9.py` — 30 passed.
- Final affected-suite verification passed: `python -m py_compile apps/api/app/services/docker_service.py apps/api/app/services/agent_runtime/runtime.py apps/api/tests/test_docker_service_exec.py && PYTHONPATH=apps/api pytest -q apps/api/tests/test_docker_service_exec.py apps/api/tests/test_single_loop_e2e.py apps/api/tests/test_react_engine_v9.py apps/api/tests/test_tool_parser_batch.py apps/api/tests/test_tool_parser.py apps/api/tests/test_react_engine_v4.py apps/api/tests/test_mcp_routing.py` — 75 passed.
- `openspec validate react-engine-batched-tool-execution --strict` and `git diff --check` both passed.

## 2026-09-14 — Post-completion MCP decimal-string ID compatibility

- Added generic MCP argument normalization driven by each selected tool's live `inputSchema`; no MCP name or tool name is used to decide field conversion.
- Exact integers are converted to strings only when the schema requires strings, or when a string/integer union receives a value beyond JavaScript's safe integer range. Nested object properties and array items are supported without mutating the original arguments.
- Applied normalization before both per-run cached MCP sessions and direct MCP calls, so dedup/materialization keys use the actual normalized arguments.
- Extended the MCP prompt catalog with bounded `field:type` information so the LLM sees `id:string` rather than only `required=[id]`.
- Pinned the existing GetNote stdio template/runtime package argument from `@getnote/mcp` to `@getnote/mcp@1.7.2`; other packages and already-versioned configurations remain unchanged. The pin is also applied to Code Agent MCP config materialization.
- Added regression coverage for large snowflake IDs, safe union integers, nested/array schema conversion, prompt type visibility, selective version pinning and Code Agent config materialization.
- Focused verification passed: `python -m py_compile ... && PYTHONPATH=apps/api pytest -q apps/api/tests/test_mcp_catalog.py apps/api/tests/test_mcp_session_manager.py apps/api/tests/test_api_dockerfile.py apps/api/tests/test_code_agent_claude_code_runtime.py` — 73 passed.
- First live schema probe did not reach MCP: application config resolved a root-relative empty SQLite database and raised `no such table: mcps`. The retry must explicitly select `apps/api/data/gap.db`.
- Re-ran focused verification after adding direct-call fallback coverage — 74 passed.
- Final affected backend verification passed — 141 passed across MCP catalog/session/routing, single-loop integration, MCP parser formats, ReAct v7/v9, Code Agent MCP materialization and Dockerfile coverage.
- Frontend production build passed after updating the GetNote MCP template to the pinned package version.
- The corrected live schema probe session closed without retaining output; deterministic schema-contract tests remain the delivery evidence rather than an environment-dependent `npx` startup.
- `openspec validate react-engine-batched-tool-execution --strict` passed and `git diff --check` reported no whitespace errors.

## 2026-09-14 — opsx:verify

- Re-read proposal, design, delta spec and all 19 task checkboxes from OpenSpec context.
- Counted 7 requirements and 18 scenarios; all 19 task checkboxes are marked complete.
- Verification execution passed: 94 affected backend tests, `npm run build`, `openspec validate react-engine-batched-tool-execution --strict`, and `git diff --check`.
- Static requirement mapping found two archive-blocking mismatches: write children can execute in `parallel`/`sequence`, and compact batch results omit required batch id/actionable failure retry hints.
- UI batch details are implemented and buildable, but no automated frontend interaction/component test exists; recorded as a warning.
- Design still calls general shell batching a non-goal while implementation supports it; recorded as a coherence warning for artifact reconciliation.

## 2026-09-14 — Archive-blocker corrections

- Added a pre-execution batch guard that rejects `file_write` and `file_search_replace` children in `parallel` or `sequence`; no child is dispatched and the response instructs the model to use `transaction`/PATCH semantics.
- Added optional caller-supplied batch identifiers plus deterministic runtime-generated identifiers. The same `batch_id` is included in model-facing `BATCH_RESULT`, persisted execution details and UI expansion content.
- Added concise failure-specific `retry_hint` values for write-mode violations, transaction shape errors, patch conflicts, permission failures, cross-domain batches, transient errors and generic child failures.
- Added transaction `done` and `skipped` summary counts so every mode exposes the required aggregate shape.
- Added regression tests for supplied batch IDs, both non-transaction write modes, zero child execution, generated IDs, retry hints and persisted detail fields.
- Initial focused run exposed and fixed two implementation regressions: lost human-readable cross-domain text and an uninitialized rejection message; both are recorded in `task_plan.md`.
- Focused parser/runtime verification now passes: 24 tests.
- Re-ran the full affected suite after final payload ordering/compaction adjustments: 96 passed.
- Re-ran the complete API test directory: 1210 passed, 4 skipped, 0 failed.
- Frontend `npm run build`, strict OpenSpec validation and `git diff --check` passed.
- Final `opsx:verify` assessment: 0 critical issues; both archive blockers are resolved. Two non-blocking coherence/test-coverage warnings remain documented.

## 2026-09-15 — Post-completion empty MCP selection recovery

- Hid native MCP call/routing schemas when an Agent has `mcp_tool_call` permission but no configured MCP bindings.
- Added bounded supplemental LLM routing for the misleading `no mcp configured` result when authorized bindings exist, followed by one immediate retry for empty-selection and missing-tool failures only.
- Added the same recovery to MCP batch children with a per-batch lock so parallel children cannot trigger duplicate routes or load every bound MCP.
- Replaced the misleading message with a typed no-selection explanation when supplementation finds no match, and classified it as a failed/dead-end tool result.
- Added regression coverage for schema suppression, single MCP retry, batch MCP retry and failure classification.
- Verification passed:
  - Focused MCP routing suite: 19 passed.
  - ReAct/batch/schema compatibility suite: 46 passed.
  - Final focused routing/stuck/batch suite after failure-classification adjustment: 58 passed.
  - Complete API suite with the required repository import path: 1213 passed, 4 skipped.
  - Python compilation and `git diff --check`: passed.

## 2026-09-15 — Final opsx:verify

- OpenSpec status/instructions: `spec-driven`, artifacts complete, 19/19 tasks complete.
- Requirement inventory: 7 requirements, 18 scenarios.
- `openspec validate react-engine-batched-tool-execution --strict`: passed.
- Affected backend suite: 97 passed, 0 failed.
- `npm run build` in `apps/web`: passed (only existing Rollup annotation and chunk-size warnings).
- `git diff --check`: passed.
- Final assessment: 0 critical issues, 2 non-blocking warnings; ready to archive after accepting or addressing those warnings.
