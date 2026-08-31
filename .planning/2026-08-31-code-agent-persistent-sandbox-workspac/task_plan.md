# Task Plan: code-agent-persistent-sandbox-workspac

## Goal

按 `openspec/changes/code-agent-persistent-sandbox-workspac/tasks.md` 实施 CodeAgent 收敛：复用编辑页绑定的持久 Sandbox，Manifest 只保存 Git 配置，Workspace 在 Sandbox 内同步仓库，UI 同步移除发布/扫描/snapshot 语义。

## Source of Truth

OpenSpec `tasks.md` 是唯一正式任务来源。本文件只映射 execution phases，不重新定义需求。

## Current Phase

Phase 5: Manifest lightweight publish and Sandbox Git sync（complete）

## Next Step

所有 OpenSpec tasks 已完成并验证；下一步可执行 `opsx:verify code-agent-persistent-sandbox-workspac` 或归档。

## Phases

### Phase 1: Persistent Sandbox and Workspace

- OpenSpec task mapping: 1.1–1.3
- Status: complete

### Phase 2: Manifest and UI convergence

- OpenSpec task mapping: 2.1–2.3
- Status: complete

### Phase 3: Remove specialized publish/runner flow

- OpenSpec task mapping: 3.1–3.3
- Status: complete

### Phase 4: Regression and documentation

- OpenSpec task mapping: 4.1–4.2
- Status: complete

### Phase 5: Manifest lightweight publish and Sandbox Git sync

- OpenSpec task mapping: 5.1–5.4
- Status: complete

## OpenSpec Task Status

| Task range | Phase | Status |
|---|---|---|
| 1.1–1.3 | Persistent Sandbox and Workspace | completed |
| 2.1–2.3 | Manifest and UI convergence | completed |
| 3.1–3.3 | Remove specialized publish/runner flow | completed |
| 4.1–4.2 | Regression and documentation | completed |
| 5.1–5.4 | Manifest lightweight publish and Sandbox Git sync | completed |
