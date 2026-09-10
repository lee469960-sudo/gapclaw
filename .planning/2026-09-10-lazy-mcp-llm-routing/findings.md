# Findings: lazy-mcp-llm-routing

## Requirements Source

- The only formal implementation source is [`openspec/changes/lazy-mcp-llm-routing/tasks.md`](../../openspec/changes/lazy-mcp-llm-routing/tasks.md).
- The change modifies the existing `agent-runtime` capability; its behavioral contract is the delta spec at `openspec/changes/lazy-mcp-llm-routing/specs/agent-runtime/spec.md`.
- No implementation task is complete at initialization time.

## Design Findings

- Current behavior eagerly enumerates every bound MCP before the main ReAct loop, creating avoidable connections, latency and context load.
- The approved design uses the Agent's LLM with candidate `name`, `tags` and `description`, but never a full tool directory, to select `0 / 1 / N` MCPs.
- Unselected MCPs must not connect, call `tools/list`, or gain a cache entry; failed routing yields an empty selection and never a full-load fallback.
- Supplementation is explicit and bounded to two attempts per run; routing results require server-side binding and permission validation.
- Routing events must be structured and redacted; cached catalogs may only belong to previously selected MCPs.

## Repository Findings

- MCP records already expose `name`, `tags` and `description`, which the planned candidate filter can use.
- The repository has a pre-existing dirty worktree across unrelated runtime, observability, documentation and planning files. Preserve those changes and scope edits to this OpenSpec change when implementation begins.
- `AgentContext` is immutable, while `AgentLoopState` is the intended per-run mutable carrier. The selected MCP set and routing audit state therefore belong in loop state (or an equivalent per-run object), not by mutating `ctx.mcp_ids`.
- The current eager paths are `SystemPromptBuilder.build_tools_desc(... mcp_ids=ctx.mcp_ids)` and `execute_action(..., mcp_ids=ctx.mcp_ids)`. Both must eventually receive the selected subset to make the lazy boundary enforceable.
- Existing MCP catalog caching is keyed only by MCP ID and TTL; it performs no I/O until `_get_mcp_tools_cached` is called. Selection gating can therefore prevent unselected MCP access without redesigning the cache for task 1.1.
- Agent binding IDs plus the `mcp_tool_call` allowed action are the available runtime authorization boundary. No per-request user resource ACL is present in `AgentContext`.
- `ToolStep` parsing supports extensible text protocol markers, but ordinary unknown actions are blocked by the Agent allow-list. A supplementation request must therefore be parsed as a dedicated internal runtime control action and consumed before the normal tool permission gate; it must not be exposed as a user-configurable external tool permission.
- `AgentLoopState` checkpoint serialization currently persists only explicit fields. If selected MCPs and supplementation counters must survive a resumed long run, their serialization requires a corresponding change in `_save_run_state` and `_load_run_state`.
- The `MCP_ROUTE: <needed capability>` parser marker is now present and correctly splits before a following `MCP:` call. Its runtime handler is deliberately still pending, so it currently remains an internal incomplete implementation detail and task 2.3 must not be marked complete.
- Explicit `MCP_ROUTE` supplementation and the two-attempt limit are implemented and tested. A completion review caught that 2.3 also requires automatic supplementation after an unavailable selected MCP or a missing selected tool; this trigger has not yet been added, so the checkbox remains open.
- MCP and Agent resource APIs both obtain MCP payloads from `MCP.to_dict()`, making it the narrow API seam for surfacing routing eligibility without duplicating response assembly.
- Route audit events are kept in `AgentLoopState` (and serialized into resumable state) plus application logs. The event schema deliberately stores only MCP IDs and redacted text summaries; trigger values are fixed classifications rather than raw tool calls.
- OpenSpec verification found two audit-contract divergences: `_redact_route_text` truncates the raw user message rather than creating a semantic summary, so any request of at most 240 characters is retained in full; and route events label all selected MCP IDs as loaded whenever `build_tools_desc` returns, even though that method handles individual `tools/list` failures internally. Both conflict with the required no-full-prompt and actual-load-result guarantees.
- Diagnostic review of a report that SQL generation accessed an MCP outside the Agent binding found no remote-MCP bypass in the active runtime: candidate construction starts from `ctx.mcp_ids`, discovery and execution use `state.selected_mcp_ids`, and supplement routing can select only from the same candidate set. However, the visible `mcp_loaded` step is generated from `ctx.mcp_names` (all configured bindings) rather than the route selection, so it falsely reports every binding as “loaded”; `build_ads_view_catalog(ctx.mcp_ids)` in plan refresh reads only an in-memory cache and can expose cached view context for configured-but-unselected MCPs. An actual unbound network access would therefore require stale/corrupt `Agent.mcps` data or another caller outside this runtime; confirm against route-event candidate/selected IDs and `connect_mcp_detail` logs.
- Read-only investigation of Agent `cbd86c0d`, session `9332d274`, shows its stored MCP binding is exactly `ads-sync-hub-mcp` and `clickhouse-docs`; no `mcp_tool_call` appears in either run. The misleading `mcp_loaded` event lists those two configured bindings, not real connections. The second user turn did perform `file_read` on the task directory, its generated `optimization_advice.md`, and generated read-result files, followed by multiple `file_search` actions. `_search_workplace` recursively scans all non-hidden text files under the Agent workplace, so it can inspect unrelated files in that shared workplace. This is separate from MCP routing and is enabled because the Agent has `file_search` permission.
- Agent creation 500 at 2026-09-10 16:49:42 was caused by schema drift: the live `agents.summary_max_words` column is NOT NULL with no database default, while the `Agent` ORM model, request body and creation assignment omitted it. The UI already supplied a default of 5000. The API now persists a clamped 100–50000 value with default 5000, and serializes the field for existing rows.
- Session UI enhancement (outside the formal lazy-MCP task checklist): `AgentChat.vue` had an execution-card collapse but every individual step always exposed inline content/snippets. Tool steps are now independently collapsible with an end-of-row disclosure arrow, default closed, while non-tool status steps retain their compact behavior.
- Session tool-detail follow-up (outside the formal lazy-MCP task checklist): the history endpoint `_steps_tail_for_message` discarded `content` for successful non-CodeAgent tools. This made a disclosure arrow rotate without an expanded body, even though the runtime had persisted a bounded result. The endpoint now returns all stored step content after `redact_code_output` and the UI shows an explicit legacy-data notice when an old step never stored output.
- Session tool-detail size decision (outside the formal lazy-MCP task checklist): a 400-character cap is not enough for SQL and query diagnostics. New tool-step details are retained and returned up to 12,000 characters, while model-context clipping is unchanged, responses are redacted at the API boundary, and the UI uses a 420px/55vh scrolling panel.
- `opsx:verify` re-run confirms task 3.2 is still materially incomplete despite its checked checkbox and passing suite: `runtime.py:_redact_route_text` emits the raw message when it is short enough, and initial/supplement call sites pass `selected_mcp_ids`/`newly_selected` as `loaded_mcp_ids` after a bulk catalog builder that can absorb individual `tools/list` failures. The audit event therefore cannot establish the required semantic prompt summary or actual per-MCP load result.
- Post-remediation `opsx:verify` found one remaining task-3.2 disclosure path: `event.reason` is sourced from the routing LLM and sent through `_redact_route_text` only. A malicious or merely verbose router can echo the full `user_message` in its reason, so the event can still contain the complete prompt. The audit writer must remove/reject an exact normalized request echo from the reason and add a regression test before archive.
- The route-reason echo gap is fixed: `_route_reason_summary` normalizes and credential-redacts both the reason and user request, replaces any complete request occurrence with `[USER_REQUEST_REDACTED]`, then bounds the result. The direct audit regression proves the persisted event contains neither the user request nor the credential values.
- User-directed audit-detail adjustment: route-reason retention is 4,000 characters (not 500). The complete-request redaction runs before this cap, so the larger audit field does not reintroduce the reason-echo disclosure path.

## Issues Encountered

| Issue | Resolution |
|---|---|
| `/Users/lizhidong/.codex/skills/planning-with-files/scripts/session-catchup.py` is not present | Did not repeat the failed command. Initialized from the current OpenSpec artifacts, existing planning layout and `git status`; record any later recovery information manually. |
| Routing eligibility fields were accidentally added to `Skill.to_dict()` during task 3.1 | Corrected before task completion by moving the fields to `MCP.to_dict()` and adding model serialization coverage. |

## Resources

- `openspec/changes/lazy-mcp-llm-routing/proposal.md`
- `openspec/changes/lazy-mcp-llm-routing/design.md`
- `openspec/changes/lazy-mcp-llm-routing/specs/agent-runtime/spec.md`
- `openspec/changes/lazy-mcp-llm-routing/tasks.md`
