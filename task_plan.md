# Plan: agent-session-capability-router

## Formal Source

`openspec/changes/agent-session-capability-router/tasks.md` is the only formal task source.

The user referenced `openspec/changes/agent-session-capability-route/`, but that directory is not present. The existing matching OpenSpec change directory is `openspec/changes/agent-session-capability-router/`; this plan follows that change unless the user directs otherwise.

This plan does not redefine requirements. It maps the OpenSpec tasks to execution phases and verification checkpoints.

## Execution phases

1. Existing route audit and regression baselines — complete
   - Maps: OpenSpec tasks 1.1, 1.2, 1.3.
   - Work: inspect current `execution_policy`, `_bound_mcp_capability_hints()`, `mcp_routing.py`, Skill loading, and tests; capture failure points and add baseline regressions before changing behavior.
   - Verify: existing MCP routing guarantees remain covered before implementation changes.

2. Routing hints model and configuration — complete
   - Maps: OpenSpec tasks 2.1, 2.2, 2.3, 2.4.
   - Work: add optional persisted routing hints only where needed, implement merge and sanitization semantics, and seed only selected built-in/test hints.
   - Verify: existing Agents without hints still load/save/run unchanged, and routing hints do not alter execution permissions.

3. Existing route matching optimization — complete
   - Maps: OpenSpec tasks 3.1, 3.2, 3.3, 3.4, 3.5.
   - Work: extend the existing pre-ReAct route path and MCP/Skill matching logic without adding an LLM classifier or replacing `mcp_routing.py`.
   - Verify: representative classifications, deterministic scoring, Skill advisory candidates, MCP authority, empty-route fallback, and safe degradation are covered by tests.

4. ReAct context integration and trace — complete
   - Maps: OpenSpec tasks 4.1, 4.2, 4.3, 4.4.
   - Work: inject advisory Candidate Capabilities only for capability-related requests and persist sanitized route traces in existing surfaces.
   - Verify: normal chat remains unpolluted; candidate context excludes secrets/tool directories; debug/read-only surfaces explain routing decisions without leaking unauthorized capability names.

5. Compatibility and final verification — complete
   - Maps: OpenSpec tasks 5.1, 5.2, 5.3, 5.4, 5.5.
   - Work: run affected runtime, execution policy, MCP/preflight, Skill, scheduled-task context, and conversation fast-path regressions; validate OpenSpec and diff hygiene.
   - Verify: implementation, regressions, compatibility, idempotency, and rollback findings are recorded before apply is considered complete.

## Completion rule

For each OpenSpec task:

1. update `progress.md` with code/test evidence;
2. confirm code and tests are complete;
3. then mark the corresponding checkbox in `openspec/changes/agent-session-capability-router/tasks.md`.
