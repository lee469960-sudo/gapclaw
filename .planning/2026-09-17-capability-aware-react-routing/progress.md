# Progress: Capability-Aware React Routing

## Session log

### Initialization

- Read the complete OpenSpec change: proposal, design, both delta specs, and tasks.
- Confirmed 7 formal task items grouped into five execution phases.
- Created the independent Planning with Files artifacts.
- No formal OpenSpec task has been marked complete yet; implementation and tests must precede each checkbox update.

### Task 1.1

- Verified capability-aware execution policy and generic resource-name classification in `apps/api/app/services/agent_runtime/execution_policy.py`.
- Test evidence: `pytest -q tests/test_execution_policy.py` → 8 passed.
- Code and tests complete; task checkbox may now be checked.

### Task 1.1 validation: capability/operation combination

- Verified capability matching requires an external-operation signal, so “帮我看看当前持仓” routes to `task` while “解释一下什么是持仓” remains `chat`.
- Test evidence: included in `pytest -q tests/test_execution_policy.py` → 8 passed.
- Code and tests complete; checkbox may now be checked.

### Task 1.1 validation: routing examples

- Verified unit coverage for implicit holdings query, conceptual explanation, and explicit `okx-trader` resource name.
- Test evidence: `pytest -q tests/test_execution_policy.py` → 8 passed.
- Code and tests complete; checkbox may now be checked.

### Task 1.2

- Added `_bound_mcp_capability_hints` to read only bound MCP metadata before mode routing; it performs no MCP connection or `tools/list` call.
- Test evidence: `pytest -q tests/test_execution_policy.py tests/test_conversation_fast_path.py` → 14 passed.
- Code and tests complete; task checkbox may now be checked.

### Task 1.2 validation: metadata failure fallback

- Metadata lookup is exception-safe and returns an empty hint set; routing falls back to fixed operation/resource signals without opening MCP connections.
- Test evidence: the metadata snapshot and existing ordinary-chat runtime tests passed in the 14-test focused run.
- Code and tests complete; checkbox may now be checked.

### Task 1.2 validation: runtime and lazy-loading regression

- Verified bound-resource ordinary conversation still uses the chat path, while metadata snapshots do not call MCP clients or load tool catalogs.
- Test evidence: `pytest -q tests/test_execution_policy.py tests/test_conversation_fast_path.py` → 14 passed.
- Code and tests complete; checkbox may now be checked.

### Task 2.1

- Explicitly named unbound resources now stop in the task path before any LLM or MCP call.
- Test evidence: `pytest -q tests/test_execution_policy.py tests/test_conversation_fast_path.py` → 14 passed, including unbound-resource integration coverage.
- Code and tests complete; task checkbox may now be checked.

### Task 2.1 validation: unbound prompt

- Unbound resource responses identify the requested MCP/tool and instruct the user to bind it before querying.
- Test evidence: unbound integration assertion passed in the 14-test focused run.
- Code and tests complete; checkbox may now be checked.

### Task 2.1 validation: terminal metric and no unauthorized call

- Unbound completion emits `route_mode=task` and `stop_reason=mcp_not_bound`; the LLM mock and MCP client are not called.
- Test evidence: unbound integration test passed in the 14-test focused run.
- Code and tests complete; checkbox may now be checked.

### Task 2.2

- Capability-aware entry routing hands task requests to the existing MCP candidate builder and LLM semantic router; authorization and lazy discovery paths remain unchanged.
- Test evidence: `pytest -q tests/test_mcp_routing.py tests/test_model_router.py tests/test_react_engine_v17.py` → 51 passed.
- Code and tests complete; task checkbox may now be checked.

### Task 2.2 validation: bound resource authorization

- Existing MCP routing tests confirm bound candidates are selected, validated, and dispatched through the established semantic routing path.
- Test evidence: the 51-test MCP/model/runtime routing suite passed.
- Code and tests complete; checkbox may now be checked.

### Task 3.1

- Focused routing, Agent Runtime, MCP candidate selection, lazy-loading, model routing, and regression tests passed.
- Test evidence: `pytest -q tests/test_execution_policy.py tests/test_conversation_fast_path.py tests/test_mcp_routing.py tests/test_model_router.py tests/test_react_engine_v17.py tests/test_react_engine_v8.py` → 81 passed.
- Code and tests complete; task checkbox may now be checked.

### Task 3.2

- Full API regression and compile validation completed.
- Test evidence: `pytest -q tests --ignore=tests/test_release_end_to_end_contract.py --ignore=tests/test_release_hook_end_to_end_contract.py --ignore=tests/test_staging_release_end_to_end_contract.py` → 1242 passed, 4 skipped, 640 existing warnings; `python -m compileall -q app` passed.
- Frontend evidence: `npm run build` completed successfully; only existing Rollup pure-comment and chunk-size warnings were emitted.
- Code and tests complete; task checkbox may now be checked.

### Task 3.3

- Representative routing metrics: ordinary concept → `chat`; implicit bound-capability query → `task`; explicit bound resource → `task`; explicit unbound resource → `task` followed by `mcp_not_bound` stop.
- Rollback switch: set per-request `message_meta.execution_mode=chat` or `task`; existing MCP lazy-loading and immutable deployment rollback remain unchanged.
- Representative command output recorded in this progress entry; code and tests complete, so the task checkbox may now be checked.

## Verification ledger

| Task | Code complete | Tests complete | `tasks.md` checked |
|---|---|---|---|
| 1.1 | complete | complete | yes |
| 1.2 | complete | complete | yes |
| 2.1 | complete | complete | yes |
| 2.2 | complete | complete | yes |
| 3.1 | complete | complete | yes |
| 3.2 | complete | complete | yes |
| 3.3 | complete | complete | yes |

## Errors encountered

None during initialization.

## Verification run (2026-09-17)

- `openspec status --change capability-aware-react-routing --json`: all 14 task checkboxes complete; all proposal/spec/design/tasks artifacts present.
- `openspec validate capability-aware-react-routing --type change --strict`: passed.
- Focused regression suite: 81 passed, 54 existing deprecation warnings.
- Scoped `git diff --check`: passed.
