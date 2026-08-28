# code-agent-project-policy Specification

## Purpose

定义项目接入 CodeAgent 所需的版本化策略、最小权限边界与 V1 安全限制，使每次运行的允许行为可审计且可回退。

## Requirements

### Requirement: CodeAgent 项目必须具有已发布的 Manifest 与有效策略

CodeAgent 修改任务 SHALL 仅在项目具有已发布的 Code Manifest，且其仓库来源、Secret 引用、冻结源码快照、精确基线 commit、镜像 digest 与部署门禁均有效时运行。Manifest SHALL 声明受管仓库、允许路径、验证策略、受信任执行环境、批准工具和资源上限；run 创建时 SHALL 冻结所用 Manifest、源码快照与全部有效策略版本。

#### Scenario: 已发布 Manifest 的项目启动 run

- **WHEN** 受管项目具有有效的已发布 Manifest、可用冻结快照和可信 runner，且任务满足所有限制
- **THEN** 系统允许创建 CodeAgent run 并记录 Manifest、精确 commit、快照标识、镜像 digest 与策略版本

#### Scenario: 缺少或失效 Manifest

- **WHEN** 项目没有已发布 Manifest，或准备阶段发现来源、Secret 引用、快照、commit、镜像或其他关键约束已失效或漂移
- **THEN** 系统不得创建 runner 或进入可写执行阶段
- **AND** 返回要求更新配置并重新发布 Manifest 的稳定结果

### Requirement: 策略必须遵循最小权限与单向收紧

平台、组织、项目、Manifest、Profile 和任务的授权、路径、工具、来源、网络、镜像与资源策略 SHALL 以交集方式合并，任何低层设置仅能收紧而不得突破高层限制；数值资源预算 SHALL 取各层最严格值。V1 默认 SHALL 限制于内部非生产 allowlist 仓库、隔离执行、默认断网和不含真实秘密的环境，平台硬安全门禁不得被项目、Manifest、任务或运行时参数绕过。

#### Scenario: 任务请求突破项目允许路径

- **WHEN** 任务允许范围包含项目 Manifest 未授权的路径
- **THEN** 有效策略排除该路径
- **AND** 对该路径的操作被拒绝

#### Scenario: 项目请求 V1 默认禁止的网络访问

- **WHEN** 项目或任务请求未同时获得平台与已发布 Manifest 批准的网络访问
- **THEN** 系统保持默认断网并拒绝该请求

#### Scenario: Manifest 请求更宽资源预算

- **WHEN** Manifest 或任务的任一资源上限高于平台、组织或项目上限
- **THEN** 有效策略采用更严格的上限
- **AND** 审计事实记录有效值及其限制来源

#### Scenario: 非内部非生产项目请求 Code run

- **WHEN** enabled 项目的环境 tier 为 production 或其他非 `internal_non_production` 值，即使其具有已发布 Manifest
- **THEN** 系统在创建 run、Workspace 或 runner 前拒绝该请求
- **AND** 返回稳定的项目环境策略拒绝原因

### Requirement: 策略拒绝、预算耗尽和高风险异常必须可审计且可停止

系统 SHALL 为授权失败、来源/凭据/ref/快照失败、路径或 mount 违规、镜像异常、秘密命中或扫描不完整、预算耗尽、取消、超时、Sandbox/Workspace 清理失败和其他安全异常记录可审计事件与稳定原因，并 SHALL 支持按全局、项目、仓库、工具、镜像或模型级别阻止新的 CodeAgent run。平台硬安全门禁和停止开关 SHALL 不得被临时任务设置绕过。

#### Scenario: 达到任务预算

- **WHEN** CodeAgent run 达到冻结的时间、轮次或资源预算
- **THEN** 系统停止新的执行动作并以 `budget_exhausted` 或对应资源限制原因结束
- **AND** 保留已允许保存的审计与工件信息

#### Scenario: 触发项目级停止开关

- **WHEN** 项目或仓库的 CodeAgent 停止开关被启用
- **THEN** 系统拒绝新的 CodeAgent run 并停止尚未进入终态的受影响 run 的新工具动作
- **AND** 不改变 Standard Agent 的可用性

#### Scenario: 安全失败具有稳定原因

- **WHEN** run 在来源导入、Workspace 准备、runner 启动、工具执行、验证或清理阶段触发安全失败
- **THEN** 系统记录阶段、稳定原因、有效策略版本和不含秘密的可行动信息
- **AND** 不将模型文本作为安全门禁已通过的证据

### Requirement: Coding Runtime 策略必须冻结并保持最小权限

系统 SHALL 在 CodeAgent run 创建时冻结 `coding_runtime`、模型路径、runtime 预算、允许 Skill、允许 MCP、shell/network/filesystem 权限和停止开关状态。`claude_code` runtime 只能通过已发布 Manifest 或受控 feature flag 显式启用；所有 runtime 权限 SHALL 继承并受现有 Manifest policy、平台硬门禁和最严格预算约束。

#### Scenario: Manifest 启用 Claude Code runtime

- **WHEN** 已发布 Manifest 或冻结任务契约显式选择 `coding_runtime=claude_code` 且 feature flag 允许
- **THEN** 系统在 run 上冻结该 runtime 选择、模型配置、预算、Skill/MCP 授权和策略来源
- **AND** 后续 runtime adapter 不得扩大这些授权

#### Scenario: Runtime 请求突破策略

- **WHEN** Claude Code runtime 请求未授权路径、未授权 shell 命令、未授权网络、未授权 MCP、未绑定 Skill 或超出预算的执行
- **THEN** 系统按现有 CodeAgent 策略拒绝该动作或终止 run
- **AND** 记录稳定且不含秘密的策略拒绝原因

### Requirement: Claude Code MVP 模型路径必须以 Cloud Claude 为正式路径

MVP 阶段系统 SHALL 仅把 Cloud Claude 配置作为 `claude_code` runtime 的正式模型路径。Local Model 或 Gateway 路由 MAY 作为实验配置存在，但 MUST NOT 成为正式 CodeAgent runtime 路径，除非通过独立 benchmark gate 证明代码理解、多文件修改、tool calling、test-fix loop、长任务稳定性、错误恢复和 context management 均达标。

#### Scenario: MVP 使用 Cloud Claude

- **WHEN** `claude_code` runtime 在 MVP 正式路径中启动
- **THEN** 系统使用受管 Cloud Claude 配置或凭证注入路径
- **AND** 不要求 Local Model/Gateway 路由可用

#### Scenario: Local Model 未达准入标准

- **WHEN** Local Model 未通过规定 benchmark gate
- **THEN** 系统不得将其作为 `claude_code` runtime 的正式模型路径
- **AND** 如允许实验运行，界面和审计必须明确标记为实验能力

### Requirement: Coding Runtime 必须支持配置级回滚

系统 SHALL 支持通过 feature flag 或 Manifest runtime 选择将新 run 从 `claude_code` 回滚到现有 CodeAgent runtime。回滚 MUST NOT 删除、覆盖或改写历史 run、transcript、audit、Verifier report 或 sealed artifact。

#### Scenario: 关闭 feature flag 回滚

- **WHEN** 管理者关闭 `claude_code` runtime feature flag
- **THEN** 新 CodeAgent run 不再启动 Claude Code runtime
- **AND** 已完成或进行过的历史 Claude Code run 仍按原始事实可审计
