## Purpose

定义 CodeAgent 为每次运行准备的受管源码工作区，确保源码基线、写入隔离、完整性与清理均可验证和追溯。

## ADDED Requirements

### Requirement: 每个 CodeAgent run 使用独立且固定基线的 Workspace
系统 SHALL 为每个 CodeAgent run 创建独立 Workspace，并从项目策略允许的仓库按任务冻结的 commit 获取源码。并发 run SHALL 不共享可写工作目录；Workspace SHALL 记录仓库标识和基线 commit。

#### Scenario: 两个 run 操作同一仓库
- **WHEN** 两个 CodeAgent run 使用同一仓库和相同或不同基线
- **THEN** 系统为它们准备彼此独立的可写 Workspace
- **AND** 任一 run 的文件写入不得出现在另一个 run 的 Workspace 中

#### Scenario: 固定基线准备
- **WHEN** CodeAgent run 开始准备 Workspace
- **THEN** Workspace 的源码对应任务契约中冻结的仓库与 commit
- **AND** 系统记录该仓库标识和 commit 作为运行事实

### Requirement: Workspace 完整性异常必须阻止交付
系统 SHALL 在写入和封存前校验 Workspace 的基线与受控状态。检测到无法解释的外部写入、基线不一致、未受控 checkout 状态或其他完整性异常时 SHALL 停止该 run，标记 `workspace_integrity_error`，且不得交付 patch。

#### Scenario: Workspace 被外部修改
- **WHEN** 系统检测到 Workspace 出现非该 run 可解释的文件写入
- **THEN** 系统停止后续代码执行并标记 `workspace_integrity_error`
- **AND** 不生成可接受的 patch 工件

### Requirement: 终止后必须清理可写 Workspace
系统 SHALL 在正常完成、取消、超时、基础设施失败或策略拒绝后清理该 run 的可写 Workspace。未成功封存为工件的临时工作区内容 SHALL 不得作为交付物。

#### Scenario: 用户取消运行
- **WHEN** 用户取消正在运行的 CodeAgent 任务
- **THEN** 系统阻止新的 Workspace 动作并执行清理
- **AND** 未封存的工作区内容不得被标记为可交付 patch

#### Scenario: 准备或启动阶段基础设施失败
- **WHEN** Workspace 创建后、runner 启动期间或进入统一执行循环前发生基础设施失败
- **THEN** 系统清理或冻结该 run 已创建的所有可写 Workspace 资源
- **AND** 未清理的部分状态必须产生可观察的清理失败，而不得保持为可运行的 `pending` 状态
