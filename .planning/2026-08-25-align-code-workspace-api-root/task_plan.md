# Task Plan: align-code-workspace-api-root（Execution Map）

## Goal

按 `openspec/changes/align-code-workspace-api-root/tasks.md` 的既定顺序实施并验证全部任务，保持该文件为唯一正式任务来源。

## Next Step

Apply 完成（5/5）。可选：UI 再跑一次 CodeAgent 初始化确认；然后 `/opsx:archive align-code-workspace-api-root`（并 sync specs 如需）。

## Current Phase

Phase 3: 清理与本地验活（complete）

## Phases

### Phase 1: 对齐 Workspace 物化根

- **OpenSpec task mapping:** 1.1–1.2
- **Depends on:** none
- **Status:** complete

### Phase 2: Mount fail-closed 接缝确认

- **OpenSpec task mapping:** 2.1
- **Depends on:** Phase 1
- **Status:** complete

### Phase 3: 清理与本地验活

- **OpenSpec task mapping:** 3.1–3.2
- **Depends on:** Phases 1–2
- **Status:** complete

## Boundaries

- `openspec/changes/align-code-workspace-api-root/tasks.md` 是唯一正式任务来源；本文件只映射 phase、依赖和执行状态，不复制或重新定义需求。
- `proposal.md`、`design.md` 与 `specs/` 提供范围、设计和行为约束；发生冲突时不得通过本文件修改正式任务。
- 本次初始化不修改业务代码、不运行 implementation task，也不预先勾选 `tasks.md`。
- 不重新 `/opsx:propose`，不回滚无关已有修改。

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
| Orphan `code_agent/runs` PermissionError on delete | 1 | `chmod -R u+w` then `rmtree` |
| `start-local.sh` left stale api.pid / empty log | 1 | Start uvicorn directly on `:8000`; `/health` OK |

## Decision Log（pointers only）

| Topic | Pointer |
|---|---|
| Why / scope | `openspec/changes/align-code-workspace-api-root/proposal.md` |
| How | `openspec/changes/align-code-workspace-api-root/design.md` |
| Behavior contract | `openspec/changes/align-code-workspace-api-root/specs/` |
| Formal checklist | `openspec/changes/align-code-workspace-api-root/tasks.md` |
| Grilled decisions | API root alignment; empty → `{data_dir}/code-agent/runs`; delete orphan `code_agent/runs`; restart API after fix |
