# Task Plan: code-agent-seal-honor-source-warn（Execution Map）

## Goal

按 `openspec/changes/code-agent-seal-honor-source-warn/tasks.md` 的既定顺序实施并验证全部任务，保持该文件为唯一正式任务来源。

## Next Step

Apply 完成（5/5）。可选 `/opsx-archive code-agent-seal-honor-source-warn`。第二枪（真改文件）不在本 change。

## Current Phase

Phase 3: Local shot-1 check（complete）

## Phases

### Phase 1: Sealer source-policy tests

- **OpenSpec task mapping:** 1.1–1.3
- **Depends on:** none
- **Status:** complete

### Phase 2: Honor warn at encapsulate

- **OpenSpec task mapping:** 2.1
- **Depends on:** Phase 1（1.1 预期在实现前为红）
- **Status:** complete

### Phase 3: Local shot-1 check

- **OpenSpec task mapping:** 3.1
- **Depends on:** Phase 2
- **Status:** complete

## Boundaries

- `openspec/changes/code-agent-seal-honor-source-warn/tasks.md` 是唯一正式任务来源；本文件只映射 phase、依赖和执行状态，不复制或重新定义需求。
- `proposal.md`、`design.md` 与 `specs/` 提供范围、设计和行为约束；发生冲突时不得通过本文件修改正式任务。
- 本次初始化不修改业务代码、不运行 implementation task，也不预先勾选 `tasks.md`。
- 不重新 `/opsx:propose`，不并进 `align-code-workspace-api-root`，不做 UI / `failure_reason` 落库 / 源仓库清理 / Agent 工具循环。

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
| API WatchFiles missed nested artifacts.py | 1 | Full uvicorn restart for 3.1 |

## Decision Log（pointers only）

| Topic | Pointer |
|---|---|
| Why / scope | `openspec/changes/code-agent-seal-honor-source-warn/proposal.md` |
| How | `openspec/changes/code-agent-seal-honor-source-warn/design.md` |
| Behavior contract | `openspec/changes/code-agent-seal-honor-source-warn/specs/` |
| Formal checklist | `openspec/changes/code-agent-seal-honor-source-warn/tasks.md` |
| Grilled decisions | Q4 C 两枪；Q5 A sealer 对齐 warn；Q6 A 本 change；Q7 A 复用 helper 不重扫 snapshot；Q8 A 仅 sealer+测试；Q9 B 测试+UI 再跑；Q10 A 先 propose |
