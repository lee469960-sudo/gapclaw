# Task Plan: local-runtime-log-noise-observability

## Goal

按 `openspec/changes/local-runtime-log-noise-observability/tasks.md` 实施本地运行日志降噪、结构化可观测性和诊断消费者兼容。

## Source of Truth

OpenSpec `openspec/changes/local-runtime-log-noise-observability/tasks.md` 是唯一正式任务来源。本文件只映射 execution phases，不重新定义需求。

## Current Phase

Phase 6: Acceptance（complete）

## Next Step

所有 OpenSpec tasks 已完成并完成验证。下一步可执行 OpenSpec verify/archive 流程。

## Phases

### Phase 0: Planning initialization

- OpenSpec task mapping: none
- Depends on: none
- Status: complete
- Notes: 已读取 proposal.md、design.md、specs/、tasks.md，并初始化本 Planning with Files 会话。

### Phase 1: Access Log Noise Control

- OpenSpec task mapping: 1.1-1.2
- Depends on: Phase 0
- Status: complete
- Verification focus: expected `check_status` HTTP 304 suppressed; non-304 `check_status` and unrelated access logs still emit.

### Phase 2: Agent Runtime Progress Logging

- OpenSpec task mapping: 2.1-2.2
- Depends on: Phase 1
- Status: complete
- Verification focus: `no_progress_hint` aggregation is operator-visible and resets after progress.

### Phase 3: Telegram Poller Observability

- OpenSpec task mapping: 3.1-3.3
- Depends on: Phase 2
- Status: complete
- Verification focus: per-channel failure streaks, compact repeated failures, recovery logs, no bot token leakage.

### Phase 4: LLM Error Classification

- OpenSpec task mapping: 4.1-4.3
- Depends on: Phase 3
- Status: complete
- Verification focus: 429/529 and transport failures produce structured, sanitized provider degradation logs.

### Phase 5: Diagnostics Consumers

- OpenSpec task mapping: 5.1-5.3
- Depends on: Phase 4
- Status: complete
- Verification focus: scripts, system-logs MCP and docs remain compatible with old logs and expose new structured classes where useful.

### Phase 6: Acceptance

- OpenSpec task mapping: 6.1-6.4
- Depends on: Phases 1-5
- Status: complete
- Verification focus: targeted backend tests, local scripts, healthcheck behavior and strict OpenSpec validation.

## OpenSpec Task Status

| Task range | Phase | Status |
|---|---|---|
| 1.1-1.2 | Access Log Noise Control | completed |
| 2.1-2.2 | Agent Runtime Progress Logging | completed |
| 3.1-3.3 | Telegram Poller Observability | completed |
| 4.1-4.3 | LLM Error Classification | completed |
| 5.1-5.3 | Diagnostics Consumers | completed |
| 6.1-6.4 | Acceptance | completed |

## Completion Rule

For each OpenSpec task:

1. Implement the scoped code/docs/test work.
2. Run the task's verification.
3. Update `progress.md` with actual evidence.
4. Only then mark the corresponding checkbox in `openspec/changes/local-runtime-log-noise-observability/tasks.md`.
