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
