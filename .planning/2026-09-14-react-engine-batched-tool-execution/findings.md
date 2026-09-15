# Findings: react-engine-batched-tool-execution

## Planning Setup

- The formal change lives at `openspec/changes/react-engine-batched-tool-execution/`.
- Required artifacts were read during initialization: `proposal.md`, `design.md`, `specs/agent-runtime/spec.md`, and `tasks.md`.
- `openspec validate react-engine-batched-tool-execution --strict` passed when the proposal artifacts were created.
- This change modifies the existing `agent-runtime` capability; it does not create a new capability.

## OpenSpec Artifact Review

- `proposal.md` frames the goal as reducing repeated LLM turns for mechanically similar tool work without hiding failures or expanding authority.
- `design.md` chooses explicit batch envelopes over fully implicit coalescing, with three modes: `parallel`, `sequence` and `transaction`.
- The delta spec adds requirements for explicit batched tool actions, dependency modes, complete-aware file reads, transactional patch-only writes, compact-but-inspectable results, preserved security/routing boundaries and bounded retry behavior.
- `tasks.md` contains 19 unchecked tasks across six groups: parser, execution engine, read behavior, transactional patch writes, observability/UI and integration safeguards.

## Constraints To Preserve

- Batching is orchestration only; it must not grant new file, MCP, sandbox, secret or tool permissions.
- Existing single tool calls must retain their current behavior.
- Transactional writes are patch-only by default and must validate all children before committing any write.
- Batch summaries may be compact for the LLM, but user/debug details must remain inspectable.
- Automatic retries must be bounded, visible and disabled for validation failures.
- MCP batching must not undermine lazy MCP routing; unselected MCPs must remain unloaded.

## Initial Discovery Needed During Apply

- Current ReAct parser and action representation are in `apps/api/app/services/tool_parser.py`. `ToolStep` is the parser-level action carrier used by both text protocol parsing and native tool-call normalization.
- Main ReAct dispatcher loop is in `apps/api/app/services/agent_runtime/runtime.py`, especially the `_run_modular` loop that calls `extract_tool_steps(...)` and iterates `tool_steps`.
- Locate existing file read/patch primitives, if any, and determine whether they are local runtime tools, Code Agent tools, or both.
- Locate execution event models used by UI rendering.
- Locate existing tests for `agent-runtime`, MCP routing, ReAct protocol parsing and conversation execution UI.

## Task 1.1 Outcome

- Added parser-level batch schema support in `tool_parser.py`.
- Supported forms are explicit `BATCH: {...}` protocol lines and whole JSON tool objects like `{"name":"BATCH","arguments":...}`.
- Valid batch envelopes normalize to `ToolStep(action="tool_batch", batch={...})` with mode `parallel`, `sequence` or `transaction` and normalized child actions/replies.
- Invalid batch envelopes normalize to `ToolStep(action="tool_batch_invalid", ...)` so later dispatch can reject without accidentally executing any child.
- Child parsing reuses existing single-action normalization for `READ`, `SEARCH`, `MCP`, and other current protocol actions; `FINAL` and `PLAN` are not accepted as executable batch children.

## Open Questions

- None that block planning. Implementation may reveal whether batch file patching can reuse an existing patch primitive or needs a new runtime-level patch adapter; record that in this file during task 4.1 discovery.

## Post-completion Docker exec 409 finding

- The literal `exec error: 409 Client Error` originates from `docker_service.exec_in_sandbox`, not from the LLM transport or shell parser.
- Serializing shell children only inside one `parallel` batch is insufficient: standalone shell calls, skill scripts and separate concurrent Agent runs all reach the same Docker exec boundary.
- Docker HTTP 409 is a container lifecycle conflict (for example exited, paused or restarting). The stable boundary is therefore per-container serialization plus a fresh status check/recovery immediately before exec.
- Recovery must be bounded: safely restore `created`, `exited` and `paused` states, wait briefly for `restarting`, and retry an actual 409 only once. Persistent failure must be surfaced as a typed sandbox failure so ReAct does not count it as successful progress.

## Post-completion MCP decimal-string ID finding

- The MCP JSON-RPC envelope IDs are small counters starting at `1`; the error is returned by the invoked MCP tool for its business argument `id`.
- GetNote returns 19-digit snowflake identifiers such as `1917542058656307880`, which exceed JavaScript's safe integer limit. Current `@getnote/mcp` requires these IDs as exact decimal strings and rejects unsafe JSON numbers.
- The current tool-catalog prompt keeps required field names but discards property types from MCP `inputSchema`, so the LLM sees `required=[id]` without learning that `id` is a string.
- `agent_tools.execute_action` currently passes parsed MCP arguments through unchanged. The fix should use the selected tool's live schema to normalize values before both cached-session and direct-call paths, without checking MCP or tool names.
- The existing GetNote UI template uses unversioned `npx -y @getnote/mcp`, allowing package contract changes on restart. Pinning the template to an explicit tested version removes this drift for new/reset configurations; existing saved rows require migration or runtime normalization to remain compatible.
- Chosen implementation boundary: normalize with the selected live tool schema inside `agent_tools.execute_action`, before either `McpSessionManager.call_tool` or `call_mcp_tool`. This also makes dedup/materialization use the normalized arguments.
- Chosen coercion rule: recursively follow object properties and array items; convert integers only when the schema requires `string`, or when a union accepts `string` and the integer exceeds JavaScript's safe range. Never stringify booleans, floats, fields without a schema, or fields declared integer-only.
- Chosen version-pin rule: replace only the exact unversioned default package argument `@getnote/mcp` with `@getnote/mcp@1.7.2`; preserve every other package and every already-versioned GetNote configuration.

## 2026-09-14 — opsx:verify findings

- Completeness metadata is clean: the spec-driven change has 19/19 checked tasks, 7 requirements and 18 scenarios, and strict OpenSpec validation passes.
- CRITICAL: batch file writes are not constrained to `transaction`. The parser accepts both `WRITE:` and `PATCH:` for every mode, while the `parallel` and `sequence` branches dispatch those children through the ordinary executor. This lets a batch bypass transaction-wide dry-run/commit behavior required by the spec.
- CRITICAL: model-facing `BATCH_RESULT` payloads do not contain a batch identifier, and failed child records do not provide the required concise actionable retry/follow-up hint. Searches found no `batch_id` or `retry_hint` implementation/test evidence.
- WARNING: the UI implementation is present (default-false detail state, click/Enter toggle, child detail renderer and arrow), and the Vite production build succeeds, but `apps/web` has no project-owned automated component/source test framework or test covering batch expansion.
- WARNING/coherence: `design.md` lists a general shell-command batch executor as a non-goal, but the parser accepts `SHELL:` children and runtime explicitly supports/serializes shell batches. This reflects a later shell optimization decision but the OpenSpec design was not reconciled.
- Current verification suite passed: 94 backend tests, frontend production build, strict OpenSpec validation and `git diff --check`.

## 2026-09-14 — opsx:verify blocker resolution

- RESOLVED: `parallel` and `sequence` now reject every `file_write` or `file_search_replace` child before dispatch. The whole batch returns `write_requires_transaction`, while `transaction` continues to reject full-file `WRITE:` and dry-run all PATCH children.
- RESOLVED: every valid batch now has a stable identifier. Caller-provided `batch_id`/`id` is preserved and sanitized; otherwise runtime generates `batch-<run_ts>-<round>-<sequence>`. It is emitted first in model-facing output and retained in persisted/UI details.
- RESOLVED: every failed child gains a concise, failure-specific `retry_hint`; failed tool output is not duplicated as both `result` and `error`, keeping the failure summary compact.
- RESOLVED: transaction summaries now include the common `done`, `error` and `skipped` counts in addition to validation/commit fields.
- Verification after correction passed: 24 focused parser/runtime tests, 96 affected backend tests, the full API suite with 1210 passed and 4 skipped, frontend production build, strict OpenSpec validation, and whitespace checks.
- Remaining non-blocking warnings are unchanged: no frontend component/interaction test infrastructure, and `design.md` still describes general shell batching as a non-goal despite the later accepted serialized shell behavior.
# 2026-09-14 — `/workspace/.openclaw` shell-path diagnosis

- Repository-wide source search found no `/workspace/.openclaw` or `.openclaw` literal in application code, prompts, configuration, or tracked assets.
- Code Agent intentionally mounts the selected repository at `/workspace`; its own runtime configuration directory is explicitly `${repository_cwd}/.claude`, normally `/workspace/.claude`.
- The generic ReAct shell executes inside its sandbox work directory and only receives a general POSIX shell schema; the current system prompt does not instruct it to use `.openclaw`.
- Therefore `/workspace/.openclaw` is not currently evidenced as an engine-defined path. The remaining likely sources are model-generated path inference or text recovered from a specific conversation/project/runtime artifact; inspect persisted tool-call records next.
- Production read-only inspection confirmed every currently running GAP ReAct sandbox starts with `WorkingDir=/workplace` and mounts only its authorized host workplace at `/workplace`; none contains `/workspace/.openclaw`. The Code Agent sandbox has an otherwise empty `/workspace`, but its active repository runtime records use `/workplace/code/<project>/workspace`, not `.openclaw`.
- Production `chat_messages`, Agent prompts/memory, Code Agent task contracts/audits/runtime facts and API logs contain no `openclaw` or `/workspace/.openclaw` occurrence. The exact transient shell command is not durably retained in those stores.
- Direct cause in the execution path: the shell tool schema accepts an arbitrary POSIX command string, and `_exec_shell` forwards that string unchanged to Docker `exec_run(["/bin/sh", "-c", command])`. Unlike file tools, shell commands receive no path normalization or workspace-root enforcement. Thus a model-generated absolute path is attempted verbatim.
- Most likely model-side cause: it conflated the separate Code Agent `/workspace` convention with the familiar hidden `.openclaw` directory name. This is an inference because the transient model output is absent from durable records, but repository and production evidence rule out an engine prompt/config hardcode.

## 2026-09-15 — `mcp_tool_call` returns `no mcp configured`

- The exact return is emitted only by `agent_tools.execute_action` after it receives the runtime `mcp_ids` list, resolves those IDs against `MCP`, and obtains zero valid rows.
- Under complete lazy routing, ordinary dispatch passes `state.selected_mcp_ids`—not the Agent's full bound `ctx.mcp_ids`—to `execute_action`. An empty initial routing decision therefore becomes indistinguishable from “the Agent has no configured MCP” at the executor boundary.
- The native `mcp_tool_call` schema is exposed whenever the permission is enabled, independent of whether lazy routing selected any MCP. The model can consequently call the meta-tool while the selected set is empty.
- Automatic MCP supplementation currently triggers only for `未在绑定 MCP 中找到工具` and connection failures. It does not treat `no mcp configured` as an empty-selection routing miss, so the failed call is not repaired automatically.
- Production confirms both contributing configurations: `dbt-test`, `Linux运维专家`, and `Harness` have `mcp_tool_call` permission but `mcps=[]`; for these Agents the message is literal and expected. Other Agents have valid, routing-eligible MCP bindings, while route audit logs show some initial decisions with a non-empty candidate set but `selected_mcp_ids=[]`; for those runs the same message is misleading and caused by the lazy-route gap.
- All six current production MCP rows have routing metadata, so missing tags/description is not the present production cause, although the candidate builder would exclude such legacy rows.
- Existing routing tests cover explicit supplement requests and missing-tool supplementation, but there is no regression test for an empty initial selection followed by `mcp_tool_call`.
- Implemented resolution: Agents with no configured MCP bindings no longer receive native `mcp_tool_call`/`mcp_route_request` schemas. Agents with bindings but an empty lazy selection trigger one bounded metadata-only LLM supplement route; when a new MCP is selected, the original call is retried exactly once because no remote invocation occurred before the empty-selection/missing-tool result.
- Batch MCP children use one per-batch async route lock. Parallel children can all observe an empty selection, but only the first performs supplemental routing; waiting children reuse the selected subset. No branch falls back to discovering or loading all bound MCPs.
- Connection-level MCP failures are deliberately not replayed automatically because the remote side-effect state may be ambiguous.

## 2026-09-15 — Final opsx:verify after empty-selection MCP repair

- Re-read every OpenSpec context artifact in full: `proposal.md`, `design.md`, `specs/agent-runtime/spec.md`, and `tasks.md`.
- Completeness remains clean: 19/19 formal tasks checked, 7 requirements and 18 scenarios; strict OpenSpec validation passes.
- The empty-selection MCP repair preserves the change's lazy-routing boundary: supplemental selection uses only Agent-bound route candidates, updates only the LLM-selected subset, retries the original no-op call once, and uses a per-batch lock to prevent duplicate parallel supplementation.
- Affected backend verification passes with 97 tests. Frontend production build and `git diff --check` also pass.
- No critical mismatch remains. Two non-blocking warnings remain: there is no project-owned frontend interaction/component test for the collapsed/expandable batch UI, and `design.md` still calls general shell batching a non-goal while runtime supports serialized shell children after the later shell optimization.
