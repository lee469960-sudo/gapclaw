# Task Plan: code-agent-v1（OpenSpec 执行计划）

<!--
  正式任务来源 = openspec/changes/archive/2026-08-23-code-agent-v1/tasks.md（唯一，勾选以它为准）。
  验收标准来源 = openspec/changes/archive/2026-08-23-code-agent-v1/specs/**/*.md（WHEN/THEN/AND）。
  设计约束来源 = openspec/changes/archive/2026-08-23-code-agent-v1/design.md。
  本文件只将 OpenSpec tasks.md 分组为执行 phase，不新增或替换需求/任务。
  每完成一个 OpenSpec task：先更新 progress.md → 确认代码与测试完成 → 再勾选 tasks.md。
-->

## Goal

完成并归档 `code-agent-v1`：在不分叉 Unified ReAct Runtime、且不改变 Standard Agent 默认行为的前提下，交付受策略约束的 Code Profile、隔离 Workspace、Code Tools、Verifier 与可审阅 patch 工作流。

## Next Step

等待用户明确进入后续 OpenSpec 阶段；当前 change 的 specs 同步与归档均已完成。

## Current Phase

Archived

## Phases

### Phase 1: Profile、项目策略与持久化基础（tasks 1.1–1.4）

- 1.1 Agent `profile` 默认兼容的模型/schema/迁移/API
- 1.2 版本化 Code Project Manifest、策略与冻结任务契约
- 1.3 平台到任务的单向收紧策略合并与拒绝原因
- 1.4 配置/任务入口的 Profile 选择与项目授权
- **Status:** completed（1.2 回归修复已验证）

### Phase 2: Unified Runtime Profile 编译与兼容性（tasks 2.1–2.4）

- 2.1 Profile resolver 与不可变 CodeExecutionContext
- 2.2 统一事件 envelope 的 Code Profile payload
- 2.3 Code run 终结、资源回收与异常可观察性
- 2.4 Standard Profile 零行为回归
- **Status:** completed

### Phase 3: 受管 Workspace 与隔离 Runner（tasks 3.1–3.4）

- 3.1 固定基线、独立 Workspace 与源码事实
- 3.2 V1 受限容器 runner 与默认断网
- 3.3 Workspace 完整性校验
- 3.4 清理与短期留存
- **Status:** completed

### Phase 4: Code Tools 与策略执行（tasks 4.1–4.4）

- 4.1 类型化读/搜/改/测工具
- 4.2 受限 shell 与只读 Git
- 4.3 确定性预检与安全反馈
- 4.4 输出脱敏与项目 ACL
- **Status:** completed

### Phase 5: 强制验证、封存与结果模型（tasks 5.1–5.5）

- 5.1 独立 Verifier
- 5.2 基线记录与验证失败分类
- 5.3 canonical diff 与完整工件封存
- 5.4 稳定终态及 UI/API 展示
- 5.5 人工审阅与下载
- **Status:** completed

### Phase 6: 安全运营、灰度与回归（tasks 6.1–6.5）

- 6.1 分层 kill switch
- 6.2 独立队列、并发/资源配额与预算可观察性
- 6.3 安全负向测试
- 6.4 evaluation 与试点观测
- 6.5 全量回归与 OpenSpec 严格校验
- **Status:** completed

### Phase 7: Verify 缺口的 fail-closed 修复（tasks 7.1–7.5）

- 7.1 Code run admission 环境门禁
- 7.2 真实容器命令冻结 deadline 与强制终止
- 7.3 Workspace/runner 启动失败补偿清理
- 7.4 全量可封存内容秘密扫描与 fail closed
- 7.5 安全负向回归、完整门禁与重新 verify
- **Status:** completed

## Execution Rules

1. 已归档 `tasks.md` 是该 change 的唯一正式任务清单；本计划不勾选任务。
2. 单个任务只有在代码完成、任务所述验证完成且 `progress.md` 已更新后，才可在 `tasks.md` 勾选。
3. 新发现、风险、环境限制和失败尝试写入 `findings.md`；每次测试结果和实际改动写入 `progress.md`。
4. 任何与 OpenSpec 需求冲突或会扩大 V1 范围的发现，暂停该任务并先更新 OpenSpec/向用户澄清。

## Errors Encountered

| Error | Resolution |
|---|---|
| 全局 catch-up 脚本路径不存在 | 使用仓库本地 `.codex/skills/planning-with-files/scripts/session-catchup.py`；本地 catch-up 无未同步报告。 |
| 初始模板替换补丁格式/上下文不匹配 | 删除模板后以完整正确内容重建；未影响 OpenSpec 或实现文件。 |
