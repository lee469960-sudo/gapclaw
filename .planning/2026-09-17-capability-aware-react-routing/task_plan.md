# Task Plan: Capability-Aware React Routing

## Goal

Implement exactly the tasks in `openspec/changes/capability-aware-react-routing/tasks.md`: use bound MCP capability metadata and explicit resource names to route external-operation requests into ReAct, while keeping conceptual chat fast and reporting unbound resources clearly.

## Source of Truth

`openspec/changes/capability-aware-react-routing/tasks.md` is the only formal task source. This plan only maps those task IDs to execution phases and does not redefine requirements.

## Current Phase

Phase 5 — Verification and rollout (complete)

## Execution Phases

### Phase 1: OpenSpec inspection and Planning with Files initialization

- [x] Read proposal.md, design.md, specs/, and tasks.md
- [x] Initialize task_plan.md, findings.md, and progress.md
- [x] Map every formal task ID to an execution phase without changing scope
- **Status:** complete

### Phase 2: Execution policy routing

- [x] Implement and verify OpenSpec 1.1 (capability-aware mode classification and generic resource-name detection)
- **Formal tasks:** 1.1
- **Status:** complete

### Phase 3: Runtime entry and unbound-resource handling

- [x] Implement and verify OpenSpec 1.2 (bound MCP metadata snapshot with no MCP I/O)
- [x] Implement and verify OpenSpec 2.1 (explicit unbound resource task-path stop)
- **Formal tasks:** 1.2, 2.1
- **Status:** complete

### Phase 4: MCP routing integration

- [x] Implement and verify OpenSpec 2.2 (preserve authorization and existing lazy semantic MCP routing)
- **Formal tasks:** 2.2
- **Status:** complete

### Phase 5: Verification and rollout

- [x] Complete and document OpenSpec 3.1 (focused routing/runtime/lazy-loading tests)
- [x] Complete and document OpenSpec 3.2 (full API regression, compile, frontend build)
- [x] Complete and document OpenSpec 3.3 (representative metrics and rollback)
- **Formal tasks:** 3.1, 3.2, 3.3
- **Status:** complete

## Decisions

- MCP names and capability metadata participate only in mode routing; MCP connections and tool catalogs remain lazy.
- A resource name is never hardcoded; matching uses bound metadata or generic resource-name patterns.
- Explicitly unbound resources enter the task path and return `mcp_not_bound`, rather than receiving a simulated chat answer.
