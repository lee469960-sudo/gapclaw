## MODIFIED Requirements

### Requirement: Local 发布策略必须单向收紧并隔离副作用

系统 SHALL 将 local 发布限制在已绑定的持久 Sandbox 内，并禁止 privileged、Docker socket、宿主挂载和 `host` 网络。策略 MUST NOT 依赖 Manifest 命令登记、平台档案或发布锁来允许或拒绝任务；用户明确请求后直接进入 Claude Code。

#### Scenario: 请求突破 local 发布策略

- **WHEN** 任务请求 privileged、Docker socket 或未批准宿主路径
- **THEN** 适配器拒绝该请求
- **AND** 记录稳定拒绝原因

#### Scenario: 同一仓库并发发布

- **WHEN** 同一项目已有 CodeAgent Claude Code 任务在绑定 Sandbox 中运行
- **THEN** 新任务遵循现有 Run 并发与 Sandbox 绑定规则
- **AND** 平台不得因缺少 Manifest 发布锁而额外拒绝
