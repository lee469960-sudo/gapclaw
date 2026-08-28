## MODIFIED Requirements

### Requirement: 每个 CodeAgent run 使用独立且固定基线的 Workspace

系统 SHALL 为每个 CodeAgent run 从已发布 Manifest 冻结的不可变源码快照创建独立可写 Workspace，并验证其仓库标识和精确基线 commit。并发 run SHALL 不共享可写工作目录；API 进程不得直接访问 Workspace 内容，所有允许的源码操作 SHALL 仅通过该 run 的专用 runner 执行。

#### Scenario: 两个 run 操作同一仓库

- **WHEN** 两个 CodeAgent run 使用同一仓库和相同或不同冻结基线
- **THEN** 系统为它们准备彼此独立的可写 Workspace
- **AND** 任一 run 的文件写入不得出现在另一个 run 的 Workspace 中

#### Scenario: 固定基线准备

- **WHEN** CodeAgent run 开始准备 Workspace
- **THEN** Workspace 的源码严格对应任务契约中的快照标识与精确 commit
- **AND** 系统记录该仓库标识、快照标识和 commit 作为运行事实，不访问原 Git 服务

#### Scenario: API 进程尝试源码操作

- **WHEN** Code Tool 无法通过该 run 的专用 runner 执行
- **THEN** 系统拒绝该动作并结束或暂停 run
- **AND** 不回退到 API 进程直接读取或修改 Workspace

### Requirement: Workspace 完整性异常必须阻止交付

系统 SHALL 在准备完成、写入期间和封存前校验 Workspace 的快照身份、基线与受控状态。检测到快照或 commit 不一致、无法解释的外部写入、跨 run 访问、未受控 checkout 状态、mount 指向错误或其他完整性异常时 SHALL 停止该 run，标记 `workspace_integrity_error`，且不得交付 patch。

#### Scenario: Workspace 被外部修改

- **WHEN** 系统检测到 Workspace 出现非该 run runner 可解释的文件写入
- **THEN** 系统停止后续代码执行并标记 `workspace_integrity_error`
- **AND** 不生成可接受的 patch 工件

#### Scenario: Workspace 基线或挂载身份不匹配

- **WHEN** Workspace 的快照、commit、run 标识或实际挂载路径与冻结契约不一致
- **THEN** 系统在工具执行前或检测后立即阻止后续动作
- **AND** 不尝试通过重新 checkout 移动 ref 来修复基线

### Requirement: 终止后必须清理可写 Workspace

系统 SHALL 在正常完成、取消、超时、基础设施失败或策略拒绝后立即撤销该 Workspace 的执行资格并移除 runner。Workspace 默认 SHALL 转为不可执行的受保护保留态 7 天用于审计与故障分析，随后按平台与项目保留策略中更严格的期限清理；策略要求立即清理时 SHALL 立即删除。封存工件和审计记录 SHALL 使用独立保留策略，未成功封存的内容不得作为交付物。

#### Scenario: 用户取消运行

- **WHEN** 用户取消正在运行的 CodeAgent 任务
- **THEN** 系统阻止新的 Workspace 动作、移除 runner 并将 Workspace 转为不可执行保留态或按策略立即清理
- **AND** 未封存内容不得被标记为可交付 patch

#### Scenario: Workspace 到期清理

- **WHEN** 不可执行 Workspace 达到其有效保留期限
- **THEN** 系统清理源码和临时数据并记录清理事实
- **AND** 不删除仍在独立保留期内的 sealed artifact、Verifier 报告或审计记录

#### Scenario: 准备或启动阶段基础设施失败

- **WHEN** Workspace 创建后、runner 启动期间或进入统一执行循环前发生基础设施失败
- **THEN** 系统撤销该 run 的所有可写执行能力，并按有效保留策略清理或转为不可执行保留态
- **AND** 未完成的清理或状态转换必须产生可观察失败，而不得保持为可运行的 `pending` 状态
