# Progress Log: code-agent-claude-run-readiness-v2

## Session: 2026-08-28

### Phase 0: Planning initialization

- **Status:** complete
- Actions taken:
  - Read `planning-with-files` skill.
  - Ran session catchup script for the workspace.
  - Read current OpenSpec change artifacts:
    - `proposal.md`
    - `design.md`
    - all files under `specs/`
    - `tasks.md`
  - Created Planning with Files directory:
    - `.planning/2026-08-28-code-agent-claude-run-readiness-v2/`
  - Created:
    - `task_plan.md`
    - `findings.md`
    - `progress.md`
- Implementation status:
  - No code implementation started.
  - No business files modified.
  - No OpenSpec task checkbox updated.

## OpenSpec Task Status

| Task range | Phase | Status | Notes |
|------------|-------|--------|-------|
| 1.1–1.3 | Direct execution entry | complete | Implemented `开始执行:<objective>` direct entry, empty objective rejection, and Standard/legacy compatibility tests |
| 2.1–2.5 | Claude Code workspace root readiness | complete | Implemented claude-only single-top-level flattening, Git root verification, flatten-aware base diff/reset, and tests |
| 3.1–3.4 | Runtime terminal results | complete | Implemented public non-patch terminal states, result/failure mapping, and sealer rejection |
| 4.1–4.3 | LLM binding guidance and readiness UI | pending | Depends on Phase 3 |
| 5.1–5.2 | Host validation output | pending | Depends on Phase 3 |
| 6.1–6.4 | Acceptance and regression | pending | Final validation |

### Phase 1: Direct execution entry

- **Status:** complete
- Completed OpenSpec tasks:
  - 1.1
  - 1.2
  - 1.3
- Code changes:
  - Added scoped `开始执行:<objective>` parsing in `apps/api/app/routers/agent_chat.py`.
  - Added optional `trigger_text` audit/run fact support in `apps/api/app/services/code_agent/control_plane.py`.
  - Added backend tests in `apps/api/tests/test_code_agent_coding_sop.py`.
- Verification:
  - Initial attempt from `apps/api` used an incorrect `apps/api/tests/...` path and failed with `file or directory not found`.
  - Correct command:
    - `pytest tests/test_code_agent_coding_sop.py::test_claude_code_direct_execute_creates_run_without_confirmation tests/test_code_agent_coding_sop.py::test_claude_code_direct_execute_rejects_empty_objective tests/test_code_agent_coding_sop.py::test_legacy_code_agent_keeps_direct_execute_as_normal_objective tests/test_code_agent_coding_sop.py::test_standard_agent_keeps_direct_execute_on_standard_chat_path tests/test_code_agent_coding_sop.py::test_claude_code_first_message_does_not_create_run tests/test_code_agent_coding_sop.py::test_claude_code_confirmation_creates_run_from_grill_history tests/test_code_agent_coding_sop.py::test_legacy_still_creates_run_on_first_message -q`
  - Result: `7 passed, 9 warnings`.

### Phase 2: Claude Code workspace root readiness

- **Status:** complete
- Completed OpenSpec tasks:
  - 2.1
  - 2.2
  - 2.3
  - 2.4
  - 2.5
- Code changes:
  - Added claude-only safe single-top-level snapshot flattening in `apps/api/app/services/code_agent/workspace.py`.
  - Added `/workspace/.git` and `git rev-parse --show-toplevel` / `git status` readiness verification for new `claude_code` runs.
  - Added `repo_root_mode`, `repo_root_ready`, and `repo_root_reason` to `WorkspaceFacts`.
  - Added flatten-aware archived base handling for workspace reset and artifact sealing.
  - Added tests in `apps/api/tests/test_code_agent_workspace_snapshot.py` and `apps/api/tests/test_code_agent_artifacts.py`.
- Verification:
  - `pytest tests/test_code_agent_workspace_snapshot.py::test_claude_code_prepare_flattens_safe_single_top_level_snapshot tests/test_code_agent_workspace_snapshot.py::test_claude_code_prepare_preserves_multi_top_level_snapshot tests/test_code_agent_workspace_snapshot.py::test_claude_code_prepare_does_not_migrate_historical_workspace tests/test_code_agent_workspace_snapshot.py::test_prepare_materializes_normal_git_worktree tests/test_code_agent_workspace_snapshot.py::test_git_metadata_changes_are_excluded_from_workspace_changed_paths -q`
    - Result: `5 passed, 1 warning`.
  - `pytest tests/test_code_agent_artifacts.py::test_flattened_claude_workspace_does_not_leak_root_rewrite_into_sealed_patch tests/test_code_agent_artifacts.py::test_runtime_only_claude_files_are_excluded_from_changed_paths_and_sealed_patch -q`
    - Result: `2 passed, 7 warnings`.
  - `pytest tests/test_code_agent_workspace_snapshot.py -q`
    - Result: `11 passed, 1 warning`.

### Phase 3: Runtime terminal results

- **Status:** complete
- Completed OpenSpec tasks:
  - 3.1
  - 3.2
  - 3.3
  - 3.4
- Code changes:
  - Added `target_not_found` and `needs_user_decision` public result handling in `apps/api/app/services/code_agent/claude_code_runtime.py`.
  - Added result labels in `apps/api/app/services/code_agent/results.py`.
  - Added failure taxonomy entries in `apps/api/app/services/code_agent/failures.py`.
  - Added early non-sealing behavior in `apps/api/app/services/code_agent/artifacts.py`.
  - Added tests in `apps/api/tests/test_code_agent_claude_code_runtime.py`, `apps/api/tests/test_code_agent_results.py`, `apps/api/tests/test_code_agent_failures.py`, and `apps/api/tests/test_code_agent_artifacts.py`.
- Verification:
  - First Phase 3 test run failed because a new artifact test was inserted before the remaining assertions of `test_unverified_or_incomplete_sealing_never_marks_partial_artifacts_ready`; fixed the test structure.
  - `pytest tests/test_code_agent_claude_code_runtime.py::test_claude_code_runtime_adapter_accepts_public_non_patch_terminal_results tests/test_code_agent_results.py::test_non_patch_terminal_results_are_distinct_and_not_adoptable tests/test_code_agent_failures.py::test_existing_internal_reasons_map_to_stable_external_reasons tests/test_code_agent_artifacts.py::test_non_patch_terminal_results_never_seal_artifacts -q`
    - Result after fix: `15 passed, 3 warnings`.
  - `pytest tests/test_code_agent_results.py::test_non_patch_terminal_results_are_distinct_and_not_adoptable -q`
    - Result: `1 passed, 1 warning`.

## Verification

Phase 1, Phase 2, and Phase 3 implementation verification passed.

## Next Step

Continue with OpenSpec task 4.1 from `openspec/changes/code-agent-claude-run-readiness-v2/tasks.md`.
