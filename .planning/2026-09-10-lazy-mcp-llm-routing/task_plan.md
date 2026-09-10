# Task Plan: lazy-mcp-llm-routing

## Source of Truth

Formal implementation scope and completion state are defined only in [`openspec/changes/lazy-mcp-llm-routing/tasks.md`](../../openspec/changes/lazy-mcp-llm-routing/tasks.md). This plan maps those task IDs to execution phases; it does not define independent requirements.

## Goal

Execute the OpenSpec change `lazy-mcp-llm-routing` while preserving `tasks.md` as the sole formal task checklist.

## Next Step

Archive the change when the user requests it.

## Current Phase

Verification complete — ready for archive on request.

## Execution Phases

| Phase | OpenSpec task IDs | Status |
|---|---|---|
| 1. Candidate metadata and routing decision | 1.1, 1.2, 1.3 | completed |
| 2. Lazy discovery and bounded supplementation | 2.1, 2.2, 2.3, 2.4 | completed |
| 3. Configuration feedback and observability | 3.1, 3.2 | completed |
| 4. Regression and final validation | 4.1, 4.2, 4.3 | completed |

## Execution Protocol

For every OpenSpec task: implement only its scoped change, run its stated verification, record the actual outcome in `progress.md`, then—and only then—mark its checkbox complete in `tasks.md`.

## Decisions Made

| Decision | Rationale |
|---|---|
| Use a dedicated `.planning/2026-09-10-lazy-mcp-llm-routing/` directory | Keeps this change's persistent execution context separate from archived and unrelated work. |
| Start Phase 1 after explicit `opsx:apply` | The user authorized OpenSpec implementation; task 1.1 remains the first unchecked formal task. |

## Errors Encountered

| Error | Attempt | Resolution |
|---|---:|---|
| Expected user-level `session-catchup.py` path was absent | 1 | Recorded in findings and proceeded from the checked-in OpenSpec artifacts and repository state; do not retry the same path. |
| First task 1.1 test used a repository-relative path from the `apps/api` workdir | 1 | Recorded in progress; next run uses the workdir-relative test path. |
