# Task Plan: react-engine-batched-tool-execution

## Source of Truth

Formal implementation scope and completion state are defined only in [`openspec/changes/react-engine-batched-tool-execution/tasks.md`](../../openspec/changes/react-engine-batched-tool-execution/tasks.md). This plan maps those task IDs to execution phases; it does not redefine requirements.

## Goal

Implement safe batched ReAct tool execution so mechanically similar read and patch work can complete with fewer LLM turns while preserving single-call compatibility, per-child authorization, compact model-facing results and inspectable UI/debug details.

## Next Step

The two archive-blocking verification gaps are resolved. The change is ready to archive with two documented non-blocking warnings: no automated frontend interaction test, and the older shell-batching non-goal text has not been reconciled with the later accepted shell optimization. OpenSpec `tasks.md` remains the only formal task source.

## Current Phase

Implementation and post-fix verification are complete. OpenSpec remains 19/19, all 7 requirements have implementation evidence, and there are no remaining critical issues.

## Execution Phases

| Phase | OpenSpec task IDs | Status |
|---|---|---|
| 1. Batch protocol and parser | 1.1–1.3 | completed |
| 2. Batched execution engine | 2.1–2.3 | completed |
| 3. Batch file read behavior | 3.1–3.3 | completed |
| 4. Transactional patch writes | 4.1–4.3 | completed |
| 5. Observability and UI | 5.1–5.3 | completed |
| 6. Prompting, safeguards and integration verification | 6.1–6.4 | completed |

## Execution Protocol

For every OpenSpec task:

1. Implement only the scoped change from `tasks.md`.
2. Run the verification stated in that checkbox or the smallest stronger equivalent.
3. Record the actual result in `progress.md`.
4. Confirm code and tests are complete.
5. Only then mark that checkbox complete in `openspec/changes/react-engine-batched-tool-execution/tasks.md`.

## Decisions Made

| Decision | Rationale |
|---|---|
| Use `.planning/2026-09-14-react-engine-batched-tool-execution/` | Keeps this change's planning state isolated from the active release deployment change. |
| Treat `tasks.md` as the only formal checklist | Planning files track execution continuity and evidence only; they do not define new requirements. |
| Modify existing `agent-runtime` capability | Batch tool execution changes ReAct runtime behavior rather than introducing a separate product capability. |
| Start with explicit batch envelopes | The approved design requires `parallel`, `sequence` and `transaction` modes instead of implicit unsafe batching. |
| Preserve single-call compatibility | Existing agents and tests must continue to work without requiring batch actions. |

## Errors Encountered

- A first read-only PostgreSQL search for `/workspace/.openclaw` had incorrect nested shell/SQL quote escaping and returned a syntax error. The query was reformulated with `strpos(...)` and then completed successfully with zero matches.
- The first MCP-routing baseline test command used a repository-relative path while already running from `apps/api`, so pytest could not find the file. The corrected invocation uses `tests/test_mcp_routing.py` from that working directory.
- A full `pytest -q` from `apps/api` collected generated Code Agent snapshots and standalone smoke scripts outside the owned test suite; collection attempted blocked ClickHouse/HTTP connections and missed repository-root `tools` imports. This is a test-scope invocation error, not a product regression. The corrected full-suite command targets `apps/api/tests` from the repository root.
- The repository-root full suite ran 1212 tests successfully but one runner-helper subprocess could not import `app`, because the child process does not inherit pytest's in-process path insertion. Re-run that test and the formal suite with `PYTHONPATH=apps/api:.`; the failure is unrelated to MCP routing logic.

| Error | Attempt | Resolution |
|---|---|---|
| Live schema probe opened the default root-relative SQLite database and failed with `no such table: mcps` | 1 | Re-run with the repository's actual `apps/api/data/gap.db` database URL; do not repeat the same command unchanged. |
| Corrected live schema probe produced no retained output before its command session closed | 2 | Do not block delivery on an environment-dependent `npx` probe; verify the exact schema normalization contract with deterministic unit and affected-suite tests. |
| Combined verification-fix patch did not match the current runtime context | 1 | No code was changed; split the correction into small file-local patches against exact current lines. |
| Structured batch finalization removed the existing human-readable cross-domain message and broke one regression test | 1 | Preserve the previous rejection text in the structured payload's `message` field before rebuilding `BATCH_RESULT`. |
| New non-transaction write rejection branch built a payload without initializing `result_text` | 1 | Add the branch's human-readable rejection message before common structured finalization. |

| Error | Attempt | Resolution |
|---|---:|---|
| None yet | 0 | Planning initialized; implementation not started. |
| New batch runtime tests passed a tuple to `ChatResult.text` because of a trailing comma in the string expression | 1 | Fixed the tests to pass plain strings before rerunning the same verification. |
| After enabling `parallel`, the previous authorized-batch preflight test still expected no execution, and the fake executor lacked `**kwargs` accepted by `execute_action` | 1 | Updated the preflight test to use `sequence` mode, patched final reflection in batch tests, and fixed the fake executor signature. |
| Range coalescing helper was inserted before closing `_run_batch_child`'s `try/except`, producing a runtime syntax error | 1 | Moved the exception handler back inside `_run_batch_child` before the range helper definitions. |
