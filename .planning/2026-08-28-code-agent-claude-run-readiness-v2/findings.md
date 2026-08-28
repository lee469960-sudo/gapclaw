# Findings: code-agent-claude-run-readiness-v2

## Source Artifacts Read

- `openspec/changes/code-agent-claude-run-readiness-v2/proposal.md`
- `openspec/changes/code-agent-claude-run-readiness-v2/design.md`
- `openspec/changes/code-agent-claude-run-readiness-v2/specs/code-agent-profile/spec.md`
- `openspec/changes/code-agent-claude-run-readiness-v2/specs/code-agent-coding-runtime/spec.md`
- `openspec/changes/code-agent-claude-run-readiness-v2/specs/code-agent-workspace/spec.md`
- `openspec/changes/code-agent-claude-run-readiness-v2/specs/code-agent-verification/spec.md`
- `openspec/changes/code-agent-claude-run-readiness-v2/specs/code-agent-operator-ui/spec.md`
- `openspec/changes/code-agent-claude-run-readiness-v2/tasks.md`

## Current Known Facts

- The active injected plan still references archived `react-engine-v18`; it is stale for this work.
- This planning session uses `code-agent-claude-run-readiness-v2`.
- The latest observed `dbt-test` Claude Code run failed before coding loop at model preflight with `llm_group_not_supported`.
- Prior evidence showed workspace business files under `workspace/gamestat/`, while `workspace/.git` was absent.
- Host-side `git -C <workspace>` can accidentally resolve the parent platform repository, so Git readiness must verify the run workspace top-level explicitly.
- `dbt-clickhouse-gamestat` Skill is out of scope for this change.
- Real Cloud Claude credentials / single LLMResource binding are operator prerequisites, not code tasks.

## Decisions Imported From Grill

- `开始执行:<objective>` is a scoped shortcut for Code Profile + `claude_code`.
- Empty `开始执行:` must not create a run.
- New `claude_code` run should make `/workspace` the business repo root.
- Only safe single-top-level snapshot layouts may be flattened.
- `/workspace/.git` must come from sanitized snapshot metadata.
- Historical run workspaces must not be migrated.
- `target_not_found` and `needs_user_decision` are public non-success terminal results.
- `target_not_found` / `needs_user_decision` must not generate patch artifacts.
- LLM repair guidance is UI/API behavior; the system must not auto-select from a Group or create credentials.
- `host-validate.sh` is user-facing text only, not a repo file and not a verifier gate.

## Open Issues To Watch During Apply

- Need to locate existing CodeAgent chat gate and ensure `开始执行:` is scoped without changing Standard Agent behavior.
- Need to inspect current workspace materialization code before implementing flattening; avoid breaking legacy or non-Claude workspaces.
- Need to define stable internal reason codes for repo root / Git metadata readiness without overloading “仓库未挂载”.
- Need to confirm where terminal status enums and UI labels are centralized before adding `target_not_found` and `needs_user_decision`.
- Need to keep tests mock-based; real `dbt-test` run remains a local/operator acceptance step.

## Errors Encountered

| Timestamp | Error | Attempt | Resolution |
|-----------|-------|---------|------------|
| 2026-08-28 | Injected planning context pointed to archived `react-engine-v18` | Planning initialization | Created separate planning directory for `code-agent-claude-run-readiness-v2` and treated injected plan as stale |
| 2026-08-28 | `pytest` reported `file or directory not found` for `apps/api/tests/...` | Ran Phase 1 tests while cwd was already `apps/api` | Re-ran with `tests/...` paths; Phase 1 tests passed |
| 2026-08-28 | `NameError: name 'root' is not defined` in `test_non_patch_terminal_results_never_seal_artifacts` | Inserted new artifact test in the middle of an existing test's remaining assertions | Moved the original assertions back into the original test; Phase 3 tests passed |
