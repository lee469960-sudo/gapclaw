# Progress Log: lazy-mcp-llm-routing

## Session: 2026-09-10

### Planning initialization

- **Status:** complete
- Actions taken:
  - Read the change's proposal, design, delta spec and `tasks.md` in full.
  - Created persistent planning files and mapped, without redefining, OpenSpec task IDs to execution phases.
  - Confirmed `tasks.md` remains the sole official checklist and all of its tasks are unchecked.
  - Inspected repository status and recorded the pre-existing dirty worktree boundary.
- Files created/modified:
  - `.planning/2026-09-10-lazy-mcp-llm-routing/task_plan.md`
  - `.planning/2026-09-10-lazy-mcp-llm-routing/findings.md`
  - `.planning/2026-09-10-lazy-mcp-llm-routing/progress.md`
  - `.planning/.active_plan`

### Phase 1: Candidate metadata and routing decision

- **Status:** in_progress
- Actions taken:
  - Read the OpenSpec apply contract and all context artifacts after apply initialization.
  - Located the eager MCP discovery and tool-execution boundaries, and recorded their state ownership constraints in `findings.md`.
  - Implemented task 1.1 with metadata-only candidate construction: it requires `mcp_tool_call`, preserves binding order, deduplicates IDs, and excludes bound MCPs with neither description nor tags.
  - Implemented task 1.2 with an Agent-LLM metadata-only route request and a strict response parser. Invalid, non-string, timed-out/error, and low-confidence responses fail closed to an empty selection; valid IDs are deduplicated and intersected with the candidate set.
  - Implemented task 1.3 by deriving explicit-name signals from the current candidate IDs and names at runtime, without a static MCP map. The parser continues to discard IDs outside that candidate set.
  - Implemented task 2.1 by routing before prompt construction and using the selected subset for the tool catalog, fallback catalog, ADS cached view context, and MCP tool executor. The original binding set is now used only to construct route candidates.
  - Verified task 2.2 with two selected MCPs: the executor locates the requested tool in the second selected MCP rather than using binding position. Existing session-manager reuse and recovery tests remain green.
  - Began task 2.3: added the parsed internal `MCP_ROUTE: <needed capability>` protocol marker and per-run supplementation counter. Runtime handling, catalog refresh, bounded-attempt behavior, and task verification remain pending; task 2.3 is intentionally not checked.
  - Partially implemented task 2.3: explicit supplemental routing is an internal control action, refreshes the catalog only when a newly selected MCP is returned, persists its selected set/counter, and stops after two supplemental attempts. The required automatic trigger for selected-MCP unavailability or missing tools remains pending, so task 2.3 was deliberately returned to unchecked.
  - Completed task 2.3: MCP tool-not-found and connection-failure results now automatically request a bounded supplement. Regression coverage proves the selected catalog expands only after the failure-triggered route, while explicit supplements remain capped at two.
  - Completed task 2.4: catalog cache keys now include `modified_at`, so a changed selected MCP refreshes its directory before TTL expiry; unselected MCPs remain outside discovery and cache access.
  - Completed task 3.1: `MCP.to_dict()` now reports whether capability metadata makes the MCP eligible for automatic routing. The MCP list and Agent resource APIs already serialize through this method; both MCP management and Agent binding UI show the missing-metadata status and explain the remedy.
  - Completed task 3.2: initial routing and actual supplement routes append a bounded structured event to loop state and the application log. Events include a redacted request summary, candidate/selected/loaded MCP IDs, reason, classified trigger, supplement index, and load result; raw prompts, tool arguments, credentials, and headers are excluded.
- Files created/modified:
  - `.planning/2026-09-10-lazy-mcp-llm-routing/task_plan.md`
  - `.planning/2026-09-10-lazy-mcp-llm-routing/findings.md`
  - `.planning/2026-09-10-lazy-mcp-llm-routing/progress.md`
  - `apps/api/app/services/agent_runtime/mcp_routing.py`
  - `apps/api/tests/test_mcp_routing.py`

## OpenSpec Task Completion

| Task | Status | Evidence |
|---|---|---|
| 1.1–1.3 | complete | Candidate filtering, LLM routing, and explicit-name input are implemented and regression-tested. |
| 2.1–2.4 | complete | Selected-only discovery, bounded supplementation, and version-aware selected cache are implemented and regression-tested. |
| 3.1 | complete | API serialization and both configuration UIs expose metadata eligibility; direct model and routing tests pass. |
| 3.2 | complete | Initial and supplement route audit events are structured, bounded, persisted for resumable runs, and redacted. |
| 4.1 | complete | Candidate, lazy selection, multi-select, explicit naming and fail-closed routing regressions passed (40 tests). |
| 4.2 | complete | Supplement limits, dispatch, selected-cache, session reuse and audit-event regressions passed (34 tests). |
| 4.3 | complete | Affected API tests, API compilation, web production build, strict OpenSpec validation and diff whitespace check all passed. |

## Test Results

| Test | Expected | Actual | Status |
|---|---|---|---|
| `openspec validate lazy-mcp-llm-routing --strict` | Proposal artifacts are valid | Passed during proposal creation | passed |
| `pytest -q apps/api/tests/test_mcp_routing.py` from `apps/api` | Run task 1.1 tests | Did not start: duplicated path relative to workdir | command-path error |
| `pytest -q tests/test_mcp_routing.py` from `apps/api` | Candidate filtering and metadata serialization | 2 passed | passed |
| `pytest -q tests/test_mcp_routing.py` from `apps/api` | Candidate routing JSON, multi-select, metadata-only prompt, invalid/low-confidence failure | 4 passed | passed |
| `pytest -q tests/test_mcp_routing.py` from `apps/api` | Explicit-name signal and bound/authorized candidate rejection | 6 passed | passed |
| `pytest -q tests/test_mcp_routing.py tests/test_mcp_catalog.py` from `apps/api` | Selected-only catalog discovery and existing MCP catalog behavior | 12 passed | passed |
| `pytest -q tests/test_mcp_routing.py tests/test_mcp_catalog.py tests/test_mcp_full_materialize.py` from `apps/api` | Multi-MCP owner dispatch and MCP catalog/materialization regressions | 20 passed | passed |
| `pytest -q tests/test_mcp_session_manager.py` from `apps/api` | Selected-MCP session reuse, recovery and cleanup regressions | 8 passed | passed |
| `pytest -q tests/test_tool_parser_mcp_formats.py` from `apps/api` | Existing MCP parser formats after adding the MCP_ROUTE protocol boundary | 11 passed | passed |
| `pytest -q tests/test_mcp_routing.py tests/test_tool_parser_mcp_formats.py tests/test_llm_native_tools.py` from `apps/api` | Supplement-only catalog growth, two-attempt limit, text/native protocol regressions | 37 passed | passed |
| `pytest -q tests/test_mcp_routing.py` from `apps/api` | Automatic supplement after missing selected tool | 11 passed | passed |
| Task 2.4 initial cache patch | Apply metadata-version cache key | Not applied: test fake-MCP constructor context differed | patch-context error |
| `pytest -q tests/test_mcp_catalog.py tests/test_mcp_routing.py` from `apps/api` | Version-invalidated selected catalog cache and lazy-routing regressions | 17 passed | passed |
| `pytest -q tests/test_mcp_routing.py` and `python -m py_compile app/models.py` from `apps/api` | MCP metadata API status and existing routing behavior | 12 passed; compilation passed | passed |
| `pytest -q tests/test_mcp_routing.py` and `python -m py_compile app/services/agent_runtime/runtime.py app/services/agent_runtime/loop_state.py` from `apps/api` | Structured initial/supplement audit events and credential redaction | 13 passed; compilation passed | passed |
| `pytest -q tests/test_mcp_routing.py tests/test_tool_parser_mcp_formats.py tests/test_llm_native_tools.py` from `apps/api` | Candidate selection, single/multi-MCP routing, explicit naming, fail-closed parsing, and text/native request regression | 40 passed | passed |
| `pytest -q tests/test_mcp_routing.py tests/test_mcp_catalog.py tests/test_mcp_full_materialize.py tests/test_mcp_session_manager.py` from `apps/api` | Supplement limits, actual MCP owner dispatch, selected catalog cache, materialization and session reuse | 34 passed | passed |
| `pytest -q tests/test_mcp_routing.py tests/test_mcp_catalog.py tests/test_mcp_full_materialize.py tests/test_mcp_session_manager.py tests/test_tool_parser_mcp_formats.py tests/test_llm_native_tools.py && python -m compileall -q app` from `apps/api` | Affected runtime/API regression suite and compilation | 61 passed; compilation passed | passed |
| `npm --prefix apps/web run build --if-present` | MCP/Agent configuration UI production build | passed (existing Rollup annotation/chunk-size warnings only) | passed |
| `openspec validate lazy-mcp-llm-routing --strict` | Change artifacts are valid | passed | passed |
| `git diff --check` | No whitespace errors in the worktree diff | passed | passed |
| `opsx:verify` evidence review | Re-check OpenSpec requirements, implementation, tests and design coherence | 12/12 tasks checked and 61 tests passed, but found two audit-event requirement divergences: full short prompts may be retained and per-MCP load outcomes are not recorded accurately | failed — fix before archive |
| Runtime MCP-access diagnosis | Trace candidate, discovery, execution, supplement and cached-view paths | No direct path can connect an unbound MCP; found misleading UI “loaded” event for every configured binding and cached view context sourced from all configured bindings during PLAN refresh | diagnosis complete; no code changed |
| Agent `cbd86c0d` session `9332d274` forensic check | Inspect persisted bindings, run steps and API log | No MCP tool call occurred; `file_search` recursively scanned the Agent workplace after SQL output was requested, in addition to reads of the generated task files | diagnosis complete; no code changed |
| Agent creation 500 repair | Restore `summary_max_words` model/request/create persistence chain required by live SQLite schema | 500 root cause eliminated; `pytest -q tests/test_react_engine_v4.py tests/test_code_agent_profile.py` passed 21 tests, Python compilation and web build passed | passed |
| `opsx:verify` re-run | Re-check tasks, delta spec, audit implementation and affected regressions | 12/12 tasks checked; 82 tests and strict validation passed; prior task-3.2 audit divergences remain unchanged | failed — archive remains blocked |
| Session tool-detail UI | Add default-collapsed, per-tool disclosure rows to historical and live execution panels | Web production build passed; tool rows expose full recorded detail only after click/Enter | passed |
| Session tool-detail history repair | Return redacted, bounded successful-tool content from message-step history and make legacy empty records visibly explain their limitation | Targeted API tests passed (2); web production build and `git diff --check` passed | passed |
| Session tool-detail capacity | Preserve and expose up to 12,000 characters per new tool step without changing LLM context clipping | Targeted API tests passed (2); Python compilation, web production build and `git diff --check` passed | passed |
| `opsx:verify` re-run | Re-evaluate all 12 OpenSpec tasks, delta requirements, design decisions and affected regression suite | 12/12 task boxes; 61 MCP/runtime tests, compileall, web build, strict validation and diff check passed. Critical task-3.2 audit gaps remain in implementation: raw short prompts in `request_summary`, and selected IDs reported as loaded without per-MCP discovery outcomes. | failed — fix before archive |
| Task 3.2 verification remediation | Replace raw request text with a shape-only audit summary; carry per-MCP catalog discovery outcomes and derive loaded IDs only from `catalog_loaded` | `pytest -q tests/test_mcp_routing.py tests/test_mcp_catalog.py tests/test_mcp_full_materialize.py tests/test_mcp_session_manager.py tests/test_tool_parser_mcp_formats.py tests/test_llm_native_tools.py` passed 62; compileall, web build, strict validation and diff check passed | passed — checkbox may be restored |
| `opsx:verify` post-remediation | Independent requirements/design/code review and affected regression run | 12/12 tasks; 62 tests, compileall, web build, strict validation and diff check passed. Request summary and per-MCP results are fixed, but an LLM-provided `reason` can still echo the full user request because it is independently redacted/truncated only. | failed — one task-3.2 audit disclosure path remains |
| Task 3.2 reason-echo remediation | Redact the complete normalized user request from LLM-supplied route reasons before event persistence; retain bounded reason context | Regression suite passed 63; compileall, web build, strict validation and diff check passed. New test proves an echoed user request becomes `[USER_REQUEST_REDACTED]`. | passed — checkbox may be restored |
| Route-audit reason capacity | Increase retained route reason from 500 to 4,000 characters while preserving prompt-echo removal | Focused route-audit regression tests passed (2); Python compilation and diff check passed | passed |
| `opsx:verify` final re-run | Re-evaluate all formal tasks, requirements, design alignment and affected regressions after 4,000-character reason adjustment | 12/12 tasks complete; 64 MCP/runtime tests, compileall, web build, strict validation and diff check passed. No critical, warning or suggestion findings. | passed — ready for archive |
| `opsx:archive` | Sync `agent-runtime` delta requirements to the main spec, validate, then archive the completed change | Main specs validation passed 21/21 before and after move; archived to `openspec/changes/archive/2026-09-10-lazy-mcp-llm-routing/`; diff check passed | passed |

## Error Log

| Timestamp | Error | Attempt | Resolution |
|---|---|---:|---|
| 2026-09-10 | User-level Planning with Files `session-catchup.py` not found | 1 | Logged in findings; proceeded from on-disk change artifacts and repository status. |
| 2026-09-10 | `pytest` test path duplicated the `apps/api` workdir | 1 | Use `pytest -q tests/test_mcp_routing.py` from `apps/api`; do not repeat the failed path. |
| 2026-09-10 | Task 2.4 patch expected a different `_FakeMCP` constructor | 1 | Read the actual test definition, then apply a targeted cache-key patch. |
| 2026-09-10 | Task 3.1 metadata fields were initially placed on `Skill.to_dict()` instead of `MCP.to_dict()` | 1 | Moved fields to MCP serialization and added a direct regression test before task completion. |
| 2026-09-10 | OpenSpec verification found task 3.2 audit implementation did not meet the no-full-prompt and actual-load-result contract | 1 | Do not archive; require a focused follow-up implementation and regression coverage before re-verification. |
| 2026-09-10 | Agent creation POST returned 500 because `agents.summary_max_words` was NULL | 1 | Added defaulted, clamped request/model persistence and regression coverage. |
| 2026-09-10 | First UI patch targeted `AgentChat.vue` twice in one patch request | 1 | Combined it into a single targeted patch; build passed. |
| 2026-09-10 | Tool-detail regression test initially captured an adjacent test's assertions | 1 | Restored those assertions to their original test and reran the focused suite successfully. |

## 5-Question Reboot Check

| Question | Answer |
|---|---|
| Where am I? | All OpenSpec requirements and final verification pass; archive is ready on request. |
| Where am I going? | Archive the change when the user requests it. |
| What's the goal? | Implement the approved lazy LLM-selected MCP routing change. |
| What have I learned? | See `findings.md`. |
| What have I done? | Completed implementation, fixed all independent audit findings, and passed final verification. |
