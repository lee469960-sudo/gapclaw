## Context

See proposal.md for motivation. Current evidence from the latest `dbt-test` Claude Code run shows that workspace files and Skill injection can complete, but the run can still stop before coding loop at model preflight (`llm_group_not_supported`). The same run also showed business files under `workspace/gamestat/` while `workspace/.git` was absent and host-side Git commands accidentally resolved to the platform repository parent, making the existing “repo mounted” probe misleading.

The previous Claude Code runtime changes are now part of main specs. This change narrows the next step to run readiness and task-entry correctness, not another redesign of CodeAgent or Claude Code runtime.

## Goals / Non-Goals

**Goals:**

- Make `开始执行:<objective>` a direct execution entry for Code Profile + `claude_code`.
- Ensure new Claude Code runs get `/workspace` as the actual business repository root.
- Preserve sanitized snapshot `.git` and verify Git top-level correctness before coding.
- Provide explicit `target_not_found` and `needs_user_decision` results for deterministic task exits.
- Improve UI/readiness diagnostics so model, repo files, Git metadata, repo root and container mount failures are distinguishable.
- Provide LLM binding repair guidance without mutating user credentials or Agent binding automatically.

**Non-Goals:**

- Do not modify `dbt-clickhouse-gamestat` Skill.
- Do not create, seed or infer Cloud Claude LLMResource.
- Do not automatically change `dbt-test.llm_id`.
- Do not migrate historical run workspaces.
- Do not require CI to call a real Claude model.
- Do not write `host-validate.sh` into the business repository.

## Decisions

### 1. `开始执行:<objective>` is an explicit execution shortcut

For Code Profile + `claude_code`, parse messages whose stripped text starts with `开始执行:`. The suffix after the first colon is the run objective after trimming whitespace. If the suffix is empty, reject before `create_code_run`. Store the original trigger text in non-secret audit/run facts.

Alternatives considered: requiring a second confirmation, treating it as normal chat, or enabling it for all Agent types. Rejected because the user’s natural operational command should start CodeAgent only in the scoped Claude Code path.

### 2. New run workspace root must be `/workspace`, not `/workspace/<repo-name>`

During new Claude Code workspace preparation, if the snapshot root contains exactly one ordinary business directory after ignoring sanitized `.git` and runtime-ignored entries, copy that directory’s contents as the workspace root. Otherwise preserve the snapshot shape and fail readiness if a single repo root cannot be proven.

Alternatives considered: keeping `/workspace/gamestat` and passing repository_root everywhere. Rejected for MVP because it creates dual path semantics for Git, dbt, Claude Code and patch display.

### 3. `.git` comes only from sanitized snapshot

Prepare `/workspace/.git` from the existing sanitized snapshot metadata and verify `git -C /workspace rev-parse --show-toplevel` resolves to `/workspace`. Do not clone during run execution.

Alternatives considered: re-cloning during run or running without `.git`. Rejected because run execution must not recontact Git services, and Claude Code needs normal Git context.

### 4. Deterministic non-success results replace ambiguous failure buckets

Add public terminal results:

- `target_not_found`: explicit target value absent from allowed search scope.
- `needs_user_decision`: multiple plausible targets or unsafe ambiguity.

Neither produces a patch. `needs_user_decision` stops the runner and leaves the workspace in normal retained, non-executable state.

Alternatives considered: reuse `verification_failed`, `policy_rejected`, or `no_change_justified`. Rejected because each miscommunicates the actual state.

### 5. LLM binding guidance is UI/API only

Keep existing fail-closed model binding checks, but make the edit/run UX guide the user to select a single LLMResource with key and model. The system must not choose a member from an LLM Group.

Alternatives considered: auto-selecting a group member or seeding credentials. Rejected because both are unsafe and environment-specific.

### 6. Host validation is user-facing text only

If a task wants host validation commands, render a final `host-validate.sh` content block. Do not write the script into the repo and do not treat host execution as automatic verifier evidence.

Alternatives considered: creating a repo file or making host validation a gate. Rejected because it pollutes the patch and crosses the sandbox boundary.

## Risks / Trade-offs

- [Single-directory stripping could hide unusual repository layout] → Strip only when the snapshot has exactly one ordinary business directory; otherwise fail readiness or preserve layout.
- [Target detection depends on task/runtime interpretation] → Keep `target_not_found` and `needs_user_decision` as runtime/result states with deterministic evidence fields, and avoid modifying Skill search behavior in this change.
- [Existing failed runs remain confusing] → Do not migrate history; improve future run output and retain historical facts as-is.
- [Provider compatibility is still not schema-enforced] → Keep validation to single LLM + key + model; preflight remains responsible for actual model reachability.
- [UI may still expose too much internal detail] → Present stable status categories and reason codes, not raw runner JSON.

## Migration Plan

1. Land code and tests behind existing Claude Code runtime feature flag behavior.
2. Restart API so chat gate, workspace preparation and result enums are active.
3. User configures `dbt-test` to bind a single usable LLMResource with key and model.
4. Re-run a local `dbt-test` validation task using `开始执行:<objective>`.
5. Acceptance is platform-level success if the run reaches Claude Code coding loop and either produces a verified patch or exits as `target_not_found` with clear evidence.
6. Rollback: disable `code_claude_code_runtime_enabled` or switch Manifest runtime to `legacy`; historical run evidence remains unchanged.

## Open Questions

None. The prior grill rounds resolved scope and acceptance decisions.
