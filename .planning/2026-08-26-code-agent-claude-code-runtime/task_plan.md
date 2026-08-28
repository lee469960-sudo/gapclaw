# Task Plan: code-agent-claude-code-runtime（Execution Map）

## Source of Truth

OpenSpec `openspec/changes/code-agent-claude-code-runtime/tasks.md` is the only formal task source.

This file does not redefine requirements. It maps OpenSpec tasks to execution phases and records dependency/order only.

## Goal

Implement the OpenSpec change `code-agent-claude-code-runtime` after explicit apply authorization.

## Current Phase

All phases complete

## Next Step

Run archive only after explicit user command.

## Execution Phases

### Phase 0: Planning initialization

- **OpenSpec task mapping:** none
- **Status:** complete
- **Purpose:** Read proposal/design/specs/tasks and initialize persistent planning files.

### Phase 1: Runtime selection and policy freeze

- **OpenSpec task mapping:** 1.1–1.3
- **Depends on:** explicit apply authorization
- **Status:** complete

### Phase 2: Trusted runner image and preflight

- **OpenSpec task mapping:** 2.1–2.3
- **Depends on:** Phase 1
- **Status:** complete

### Phase 3: ClaudeCodeRuntimeAdapter

- **OpenSpec task mapping:** 3.1–3.4
- **Depends on:** Phase 1, Phase 2
- **Status:** complete

### Phase 4: Skill and MCP injection

- **OpenSpec task mapping:** 4.1–4.4
- **Depends on:** Phase 3
- **Status:** complete

### Phase 5: Runtime events, transcript and redaction

- **OpenSpec task mapping:** 5.1–5.3
- **Depends on:** Phase 3, Phase 4
- **Status:** complete

### Phase 6: Verifier and Sealer integration

- **OpenSpec task mapping:** 6.1–6.4
- **Depends on:** Phase 3, Phase 5
- **Status:** complete

### Phase 7: Operator UI

- **OpenSpec task mapping:** 7.1–7.3
- **Depends on:** Phase 1, Phase 5
- **Status:** complete

### Phase 8: MVP validation

- **OpenSpec task mapping:** 8.1–8.6
- **Depends on:** Phases 1–7
- **Status:** complete

## Per-Task Completion Gate

For each OpenSpec task:

1. Complete the implementation or artifact required by that exact task.
2. Run the task's stated verification command, test, or observable check.
3. Update `progress.md` with files changed, verification evidence, and result.
4. Only then mark the corresponding checkbox in `openspec/changes/code-agent-claude-code-runtime/tasks.md`.

## Boundaries

- Do not implement before explicit `opsx:apply code-agent-claude-code-runtime` or equivalent user authorization.
- Do not treat this file as a requirements source.
- Do not mark OpenSpec tasks complete from intent alone; require code/artifact and verification evidence.
