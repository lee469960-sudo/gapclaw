## Purpose

定义项目接入 CodeAgent 所需的版本化策略、最小权限边界与 V1 安全限制，使每次运行的允许行为可审计且可回退。

## ADDED Requirements

### Requirement: CodeAgent 项目必须具有已发布的 Manifest 与有效策略
CodeAgent 修改任务 SHALL 仅在项目具有已发布的 Code Manifest 时运行。Manifest SHALL 声明受管仓库、允许路径、验证策略、受信任执行环境、批准工具和资源上限；run 创建时 SHALL 冻结所用 Manifest 与策略版本。

#### Scenario: 已发布 Manifest 的项目启动 run
- **WHEN** 受管项目具有有效的已发布 Manifest，且任务满足其限制
- **THEN** 系统允许创建 CodeAgent run 并记录 Manifest 与策略版本

#### Scenario: 缺少或失效 Manifest
- **WHEN** 项目没有已发布 Manifest，或准备阶段发现其关键约束已漂移
- **THEN** 系统不得进入可写执行阶段
- **AND** 返回要求发布或更新 Manifest 的结果

### Requirement: 策略必须遵循最小权限与单向收紧
平台、组织、项目、Profile 和任务策略 SHALL 按从高到低的顺序合并，低层仅能收紧而不得突破高层限制。V1 默认 SHALL 限制于内部非生产 allowlist 仓库、隔离执行、默认断网和不含真实秘密的环境。

#### Scenario: 任务请求突破项目允许路径
- **WHEN** 任务允许范围包含项目 Manifest 未授权的路径
- **THEN** 有效策略排除该路径
- **AND** 对该路径的操作被拒绝

#### Scenario: 项目请求 V1 默认禁止的网络访问
- **WHEN** 项目或任务请求未获平台批准的通用网络访问
- **THEN** 系统保持默认断网并拒绝该请求

#### Scenario: 非内部非生产项目请求 Code run
- **WHEN** enabled 项目的环境 tier 为 production 或其他非 `internal_non_production` 值，即使其具有已发布 Manifest
- **THEN** 系统在创建 run 或可写 Workspace 前拒绝该请求
- **AND** 返回稳定的项目环境策略拒绝原因

### Requirement: 策略拒绝、预算耗尽和高风险异常必须可审计且可停止
系统 SHALL 为策略拒绝、预算耗尽、取消和安全异常记录可审计事件与稳定原因，并 SHALL 支持按全局、项目、仓库、工具、镜像或模型级别阻止新的 CodeAgent run。平台硬安全门禁 SHALL 不得被临时任务设置绕过。

#### Scenario: 达到任务预算
- **WHEN** CodeAgent run 达到冻结的时间、轮次或资源预算
- **THEN** 系统停止新的执行动作并以 `budget_exhausted` 结束
- **AND** 保留已允许保存的审计与工件信息

#### Scenario: 触发项目级停止开关
- **WHEN** 项目或仓库的 CodeAgent 停止开关被启用
- **THEN** 系统拒绝新的 CodeAgent run
- **AND** 不改变 Standard Agent 的可用性
