## ADDED Requirements

### Requirement: Local 发布策略必须单向收紧并隔离副作用

系统 SHALL 将 local 发布的命令、环境、路径、网络、Secret、资源预算和并发限制按最严格策略合并。策略 MUST 禁止非 local 环境、提权、任意 Shell、Docker Socket、Git 提交和未经确认的外部副作用。

#### Scenario: 请求突破 local 发布策略

- **WHEN** 任务请求非登记命令、其他环境、未授权网络或额外路径
- **THEN** 有效策略拒绝该请求
- **AND** 记录策略来源与稳定拒绝原因

#### Scenario: 同一仓库并发发布

- **WHEN** 同一仓库已有 local 发布 Run 持有发布锁
- **THEN** 新 Run 不得启动正式发布
- **AND** 返回可行动的并发冲突状态
