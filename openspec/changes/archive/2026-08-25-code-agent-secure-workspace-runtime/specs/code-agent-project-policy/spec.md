## MODIFIED Requirements

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
