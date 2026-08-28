# Task Plan: code-agent-control-plane-ui（OpenSpec 执行计划）

<!--
  正式任务来源 = openspec/changes/archive/2026-08-23-code-agent-control-plane-ui/tasks.md（唯一，勾选以它为准）。
  验收标准来源 = openspec/changes/archive/2026-08-23-code-agent-control-plane-ui/specs/**/*.md（WHEN/THEN/AND）。
  设计约束来源 = openspec/changes/archive/2026-08-23-code-agent-control-plane-ui/design.md。
  本文件只将 OpenSpec tasks.md 映射为 execution phases，不新增、替换或重新定义需求。
  每完成一个 OpenSpec task：先更新 progress.md → 确认代码与测试完成 → 再勾选 tasks.md。
-->

## Goal

完成并归档 `code-agent-control-plane-ui`；需求内容和完成条件始终以已归档 OpenSpec 原文为准。

## Next Step

等待用户明确进入 OpenSpec 阶段；尚未创建后续安全运行时、Skill Context 或使用验证 change。

## Current Phase

Archived

## Execution Phases

### Phase 1: 控制面契约与权限基础（tasks 1.1–1.3）

- 对应 OpenSpec tasks：1.1、1.2、1.3
- **Status:** completed

### Phase 2: Code Project 与 Manifest 生命周期（tasks 2.1–2.4）

- 对应 OpenSpec tasks：2.1、2.2、2.3、2.4
- **Status:** completed

### Phase 3: Code Project 管理界面（tasks 3.1–3.3）

- 对应 OpenSpec tasks：3.1、3.2、3.3
- **Status:** completed

### Phase 4: Agent 配置与运行呈现（tasks 4.1–4.4）

- 对应 OpenSpec tasks：4.1、4.2、4.3、4.4
- **Status:** completed

### Phase 5: 集成与交付验证（tasks 5.1–5.2）

- 对应 OpenSpec tasks：5.1、5.2
- **Status:** completed

## Execution Rules

1. 已归档 `tasks.md` 是该 change 的唯一正式任务清单；本计划不维护第二套任务勾选。
2. 单个任务只有在实现完成、该任务要求的代码与测试均确认通过、且 `progress.md` 已记录实际证据后，才可在 `tasks.md` 勾选。
3. 调查发现、风险、失败尝试、环境限制和待决事项写入 `findings.md`。
4. 实际改动、测试命令与结果、OpenSpec 勾选顺序写入 `progress.md`。
5. 若执行发现与 proposal/design/specs/tasks 冲突，先暂停相关任务并更新 OpenSpec 或向用户澄清，不在本计划中创造替代需求。

## Errors Encountered

| Error | Resolution |
|---|---|
| 初始化前 active plan 指向 `code-agent-v1` | 保留旧计划并为本 change 建立独立目录，随后切换 active plan。 |
| 初次人工汇总将正式任务误记为 17 项 | 以 OpenSpec CLI 与 phase 求和复核，实际为 16 项（3+4+3+4+2）；已纠正 findings/progress。 |
