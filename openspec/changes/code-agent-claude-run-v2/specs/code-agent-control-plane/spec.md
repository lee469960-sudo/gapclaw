## ADDED Requirements

### Requirement: Manifest 必须声明 local 发布命令与确认策略

启用 local 发布时，Manifest SHALL 声明唯一的 `local_publish_command_id`、固定 local target、命令依赖镜像 digest、网络目的地、Secret 引用、验证计划和并发锁策略。未声明或未通过策略校验的配置不得用于创建 local 发布 Run。

#### Scenario: 发布有效 Manifest

- **WHEN** Manifest 包含已批准的 local 命令、镜像、网络、Secret 和验证配置
- **THEN** 控制面允许其创建 local 发布 Run
- **AND** 将这些字段纳入不可变 Run 契约

#### Scenario: 发布配置缺失

- **WHEN** Manifest 缺少命令、target、凭证、网络或验证约束
- **THEN** 控制面拒绝创建 local 发布 Run
- **AND** 返回字段级可行动原因
