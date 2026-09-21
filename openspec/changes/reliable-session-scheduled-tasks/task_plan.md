# Execution Plan: reliable-session-scheduled-tasks

## Authority and operating rules

- The sole formal implementation task list is [`tasks.md`](tasks.md). This file maps those task IDs to execution phases; it does not add, alter, or complete requirements.
- A task is complete only after its code and stated verification are complete, its result is recorded in [`progress.md`](progress.md), and then its checkbox is updated in `tasks.md`.
- Discoveries, assumptions, failed attempts, compatibility findings, and unresolved risks belong in [`findings.md`](findings.md).

## Execution phases

| Phase | OpenSpec task IDs | Status | Exit condition |
|---|---|---|---|
| 1. Foundation and migration | 1.1–1.3 | complete | Models and legacy migration have passed their stated checks. |
| 2. Configuration and API | 2.1–2.5 | complete | Session-scoped API, authorization, summaries and legacy compatibility passed their stated checks. |
| 3. Scheduler reliability | 3.1–3.6 | complete | Runnable Workers, reliability guarantees and observability passed their stated checks. |
| 4. Runtime and delivery | 4.1–4.4 | complete | Formal session execution, writeback, recovery and isolated notification delivery passed regressions. |
| 5. Conversation UI | 5.1–5.4 | complete | Added empty-trigger/output guards and persisted Worker steps displayed in the conversation; backend regressions and frontend build passed. Local live acceptance requires restarted Workers. |
| 6. Release verification | 6.1–6.4 | in_progress | Tasks 6.1–6.2 are recorded; 6.3 staging rollout and 6.4 final verification remain. |

## Current phase

Phase 6 — OpenSpec task 6.3 requires staging enable/disable evidence before final verification.

## Known constraints

- OpenSpec tasks 1.1–5.3 and 6.1–6.2 are complete; tasks.md remains the sole authority for the remaining scope.
- The implementation must preserve ordinary conversation, existing Agent, bound/unbound Skill/MCP, cache, checkpoint and channel behavior as specified in `tasks.md` and `design.md`.
- Production uses PostgreSQL and development may use SQLite; both are formal verification targets.
