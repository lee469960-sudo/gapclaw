# Task Plan: code-agent-claude-run-v2

## Goal

按 `openspec/changes/code-agent-claude-run-v2/tasks.md` 实现受控的 CodeAgent local 发布能力。本文件仅将 OpenSpec tasks 映射为 execution phases，不重新定义需求。

## Source of Truth

OpenSpec `tasks.md` 是唯一正式任务来源：`openspec/changes/code-agent-claude-run-v2/tasks.md`。

## Current Phase

Phase 0: Planning initialization（complete）

## Next Step

开始执行 Phase 1；每个 OpenSpec task 均须先完成代码/测试或交付物并验证，再更新 `progress.md`，最后勾选 `tasks.md`。

## Phases

### Phase 0: Planning initialization

- OpenSpec task mapping: none
- Status: complete

### Phase 1: Local 发布契约与控制面

- OpenSpec task mapping: 1.1–1.4
- Depends on: Phase 0
- Status: pending

### Phase 2: Sandbox 与工具链

- OpenSpec task mapping: 2.1–2.4
- Depends on: Phase 1
- Status: pending

### Phase 3: 发布执行与证据

- OpenSpec task mapping: 3.1–3.4
- Depends on: Phase 2
- Status: pending

### Phase 4: Verifier 与用户界面

- OpenSpec task mapping: 4.1–4.4
- Depends on: Phase 3
- Status: pending

### Phase 5: 集成验收与回滚

- OpenSpec task mapping: 5.1–5.4
- Depends on: Phase 4
- Status: pending

## Boundaries

- 复用现有 Task、Manifest、Repository、Workspace、Sandbox、Skill/MCP、Verifier 和审计架构。
- 仅支持 Manifest 登记的 canonical local 发布命令；不实现任意 CI 执行器。
- 不支持 dev/pre/prod 发布，不自动执行 Git 提交，不自动重试副作用操作。

## Per-Task Completion Gate

每完成一个 OpenSpec task：完成实现或交付物 → 运行并确认对应验证 → 更新 `progress.md` → 记录发现到 `findings.md`（如有）→ 最后勾选 `tasks.md`。
