# Task Plan: code-agent-workspace-conversation-ui

## Goal

按 `openspec/changes/code-agent-workspace-conversation-ui/tasks.md` 实施 CodeAgent 专属 Workspace 预览、运行过程展示和最终任务结果展示。

## Source of Truth

OpenSpec `tasks.md` 是唯一正式任务来源。本文件只映射 execution phases，不重新定义需求。

## Current Phase

Phase 5: Acceptance and Documentation（complete）

## Next Step

Change 已归档；如需后续调整，应创建新的 OpenSpec change。

## Phases

### Phase 0: Planning initialization

- OpenSpec task mapping: none
- Status: complete

### Phase 1: CodeAgent Workspace API

- OpenSpec task mapping: 1.1–1.4
- Depends on: Phase 0
- Status: complete

### Phase 2: CodeAgent Conversation Progress

- OpenSpec task mapping: 2.1–2.3
- Depends on: Phase 1
- Status: complete

### Phase 3: Frontend Workspace and Conversation UI

- OpenSpec task mapping: 3.1–3.4
- Depends on: Phase 2
- Status: complete

### Phase 4: Integration, Security, and Compatibility

- OpenSpec task mapping: 4.1–4.4
- Depends on: Phases 1–3
- Status: complete

### Phase 5: Acceptance and Documentation

- OpenSpec task mapping: 5.1–5.4
- Depends on: Phases 1–4
- Status: complete

## Boundaries

- 不改变通用 Agent `/workplace`。
- 不新增 Sandbox、Runner 或仓库获取实现。
- CodeAgent 使用 Run 绑定的 `/workspace`。
- 不允许前端直接写 Workspace。

## Per-Task Completion Gate

每完成一个 OpenSpec task：完成代码/测试或交付物 → 运行并确认验证 → 更新 `progress.md` → 核对证据 → 最后勾选 `tasks.md`。
