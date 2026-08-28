# Task Plan: react-engine-v18（Execution Map）

## Goal

按 `openspec/changes/react-engine-v18/tasks.md` 的既定顺序实施并验证全部任务，保持该文件为唯一正式任务来源。

## Next Step

已 archive 到 `openspec/changes/archive/2026-08-28-react-engine-v18/`。主 spec 已同步。`runtime.py` 变更需整进程重启 API 才会在对话里生效。

## Current Phase

Phase 3: 测试与回归（complete）

## Phases

### Phase 1: Helpers and loop state

- **OpenSpec task mapping:** 1.1–1.2
- **Depends on:** none
- **Status:** complete

### Phase 2: `_run_modular` 完成度路径

- **OpenSpec task mapping:** 2.1–2.4
- **Depends on:** Phase 1
- **Status:** complete

### Phase 3: 测试与回归

- **OpenSpec task mapping:** 3.1–3.3
- **Depends on:** Phase 2
- **Status:** complete

## Boundaries

- `openspec/changes/react-engine-v18/tasks.md` 是唯一正式任务来源；本文件只映射 phase、依赖和执行状态，不复制或重新定义需求。
- `proposal.md`、`design.md` 与 `specs/` 提供范围、设计和行为约束；发生冲突时不得通过本文件修改正式任务。
- 本 planning 目录在 apply 已落地之后初始化；不重新 `/opsx:propose`，不取消已有 `tasks.md` 勾选，不并进 Code Profile / 独立裁判模型。
- 对尚未完成的 task：先写 `progress.md`、确认代码与测试，再勾选 `tasks.md`。任何一步缺失时该 task 保持未勾选。

## Per-Task Completion Gate

对每一个 OpenSpec task 必须严格执行：

1. 完成该 task 对应代码或交付物。
2. 运行该 task 声明的测试、命令或可观察验证，并确认通过。
3. 在 `progress.md` 写入实际变更、验证命令和结果。
4. 重新核对代码与测试证据后，最后勾选 `tasks.md` 对应 checkbox。

任何一步缺失时，该 task 保持未勾选。

## Errors Encountered

| Error | Attempt | Resolution |
|---|---:|---|
| （none this init） | — | — |

## Decision Log（pointers only）

| Topic | Pointer |
|---|---|
| Why / scope | `openspec/changes/react-engine-v18/proposal.md` |
| How | `openspec/changes/react-engine-v18/design.md` |
| Behavior contract | `openspec/changes/react-engine-v18/specs/agent-runtime/spec.md` |
| Formal checklist | `openspec/changes/react-engine-v18/tasks.md` |
| D1–D4 | `openspec/changes/react-engine-v18/design.md` Decisions |
