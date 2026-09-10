# Task Plan: model-router-role-groups

## Source of Truth

Formal implementation scope and completion state are defined only in [`openspec/changes/model-router-role-groups/tasks.md`](../../openspec/changes/model-router-role-groups/tasks.md). This plan maps those task IDs to execution phases; it does not redefine requirements.

## Goal

Implement the approved model-router-role-groups OpenSpec change while preserving direct LLM, legacy LLM Group, Claude Code, and embedding compatibility contracts.

## Next Step

The change has been synced to main specs and archived.

## Current Phase

Archive completed — OpenSpec tasks 1.1–6.3 are preserved under the dated archive.

## Execution Phases

| Phase | OpenSpec task IDs | Status |
|---|---|---|
| 1. Routing data model and compatible configuration API | 1.1, 1.2, 1.3 | completed |
| 2. Constrained model routing service | 2.1, 2.2, 2.3 | completed |
| 3. Standard/React runtime integration and safe degradation | 3.1, 3.2, 3.3, 3.4 | completed |
| 4. CodeAgent and embedding compatibility boundaries | 4.1, 4.2 | completed |
| 5. Management and execution-detail UI | 5.1, 5.2, 5.3, 5.4 | completed |
| 6. End-to-end verification and rollout safeguards | 6.1, 6.2, 6.3 | completed |

## Execution Protocol

For every OpenSpec task: implement only its scoped change, run its stated verification, record the actual outcome in `progress.md`, then—and only then—mark its checkbox complete in `tasks.md`.

## Decisions Made

| Decision | Rationale |
|---|---|
| Use `.planning/2026-09-10-model-router-role-groups/` | Keeps this change separate from the archived lazy-MCP plan. |
| Keep `tasks.md` as the only formal checklist | Planning files provide execution continuity only; they do not redefine scope or acceptance criteria. |
| Do not start implementation during initialization | This request authorizes artifact review and planning setup only; `opsx:apply` is required before task 1.1. |

## Errors Encountered

| Error | Attempt | Resolution |
|---|---:|---|
| Initial active-plan patch expected a path while the pointer stores only a change name | 1 | Read its exact contents and applied a targeted name-only update. |
| Attempted `openspec instructions verify` although this spec-driven schema has no `verify` artifact | 1 | Used `openspec instructions apply` for artifact context and `openspec validate model-router-role-groups --strict` for CLI validation. |
| `openspec validate --archived --strict` reports three pre-existing incomplete archived changes | 1 | Confirmed `2026-09-11-model-router-role-groups` itself passes; did not alter unrelated archives. |
| `rg` interpreted an unchecked-task pattern beginning with `-` as an option | 1 | Use `rg --` before the pattern for the final archive check. |

## 5-Question Reboot Check

| Question | Answer |
|---|---|
| Where am I? | The model-router-role-groups change is archived. |
| Where am I going? | Await the next change or follow-up request. |
| What's the goal? | Deliver configurable, auditable, task-frozen LLM routing with safe fallback and compatibility boundaries. |
| What have I learned? | See `findings.md`. |
| What have I done? | Synced all four delta specs to main specifications and archived the completed change. |
