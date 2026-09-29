# Findings: agent-session-capability-router

## 2026-09-28 — Planning initialization

- The user requested `openspec/changes/agent-session-capability-route/`, but that directory does not exist in the workspace.
- The existing matching change directory is `openspec/changes/agent-session-capability-router/`, and its artifacts were read for planning.
- The change is intentionally framed as optimizing existing routing, not creating a competing router:
  - existing entry mode classification uses `execution_policy` and `_bound_mcp_capability_hints()`;
  - existing MCP selection uses `app/services/agent_runtime/mcp_routing.py`;
  - existing Skill execution paths use current Skill loading and `SKILL_MD` / `RUN_SKILL` mechanisms.
- Current OpenSpec constraints require all bound callable MCPs to remain eligible for existing MCP semantic routing and empty-route fallback, even when optional routing hints are absent.
- A planning artifact issue was observed: `openspec/changes/agent-session-capability-router/specs/agent-runtime/spec.md` currently contains a duplicated scenario titled `Skill candidate does not load Skill content`. This should be handled deliberately during subsequent apply/verify work rather than silently ignored.
- Existing unrelated dirty files were present before this planning initialization:
  - `apps/api/app/services/docker_service.py`
  - `apps/api/tests/test_docker_service_exec.py`

## 2026-09-28 — Apply audit and implementation findings

- Existing route audit:
  - `execution_policy.classify_request()` already chooses chat/task/tool/human_wait deterministically, but prior capability hints only considered a narrow name/tags/description shape and did not expose optional aliases/keywords/type/id.
  - `AgentRuntime._bound_mcp_capability_hints()` previously read only MCP metadata; bound Skills could still fall into chat fast path unless their loaded Skill name appeared in the message.
  - `mcp_routing.build_mcp_route_candidates()` already preserves the critical guarantee that all bound callable MCPs remain eligible, including MCPs with missing metadata, and `apply_empty_route_fallback()` still expands an empty route to all eligible MCPs.
  - Existing Skill loading remains separate through `load_skill_mds` / `SKILL_MD` / `RUN_SKILL`; the new router must not read Skill.md by itself.
- Implementation findings:
  - Added optional persisted routing hints on MCP, Skill, and Agent binding overrides. Hints are advisory metadata only and do not modify credentials, transport, Skill content, or execution permissions.
  - `capability_router.py` builds effective bound MCP/Skill profiles from existing bindings, sanitizes malformed hints, merges Agent overrides, and returns only advisory candidates. It never connects MCP, calls `tools/list`, reads Skill.md, extracts parameters, or grants permission.
  - Short aliases need token-boundary handling. A bug was found during tests where alias `ck` could be matched inside `ClickHouse`, and mixed alias `sql优化` compacted to `sql`; both were fixed so compact explicit matching only applies to pure ASCII identifiers of length >= 3.
  - Candidate routing is injected only for `capability_candidate` / `explicit_capability_request`; ordinary chat keeps no Candidate Capabilities block and does not expand tools or load Skills because of the router.
  - The existing MCP semantic router remains authoritative: advisory TopK candidates do not shrink `build_mcp_route_candidates()` or empty-route fallback.
  - Route traces are stored in existing message metadata and execution steps, then allowlisted through the agent chat history API and rendered in the existing execution-step drawer. Trace payloads are bounded and exclude prompts, credentials, tool directories, and Chain of Thought.
  - Seed routing hints were added only for selected built-in demo capabilities (`system-logs`, `system-log-analyst`, `tushareMcp`, `tushare-data`), and the seed path fills only empty routing hints to avoid overwriting operator/user edits.
- Compatibility and rollback:
  - Existing Agents without routing hints continue to load/save/run because new DB columns default to `{}` and old MCP route eligibility still uses the existing bound MCP pool.
  - Missing capability requests stop before LLM with the existing `mcp_not_bound` stop reason for compatibility, while the user-facing copy now mentions MCP/Skill/工具.
  - Disabled or unpermitted bound capability matches stop before LLM with `capability_blocked`.
  - Rollback can ignore the new persisted routing fields and remove/bypass candidate-context injection; stored hints are inert without runtime use.

## 2026-09-28 — `r_info` false missing-capability regression

- User reported that the SKILL 专家 Agent still failed to route to Skill and returned: `当前 Agent 未绑定请求中的 MCP/Skill/工具：r_info。请先绑定后再执行查询。`
- Root cause: `extract_named_resource_mentions()` treated any ASCII token after `使用/调用/通过/use/call/via` as an explicit external capability name. This was correct for slug-like capability names such as `okx-trader`, but too broad for ordinary function/field identifiers such as `r_info` inside Skill-expert prompts.
- Fix: keep explicit unbound-resource detection for app-style capability names (`okx-trader`, `ads-sync-hub`, `xxxMcp`, dotted names), but exclude underscore-only identifiers without slug/service suffixes. This prevents `r_info` from triggering `missing_capability` while preserving the existing unbound MCP stop for `okx-trader`.
- Regression coverage:
  - `test_function_like_identifier_is_not_treated_as_unbound_resource_name`
  - `test_function_like_identifier_does_not_stop_as_unbound_capability_before_react`
