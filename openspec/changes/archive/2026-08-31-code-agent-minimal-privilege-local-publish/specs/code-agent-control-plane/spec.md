## MODIFIED Requirements

### Requirement: Manifest 必须声明 local 发布命令与确认策略

Manifest SHALL NOT 声明或消费 local 发布命令、target、网络目的地、Secret 引用、验证计划或并发锁。启用 local 发布时，控制面 MUST 忽略历史数据库列中的上述字段，且不得把它们纳入新 Run 契约。

#### Scenario: 发布有效 Manifest

- **WHEN** 已发布 Manifest 仅包含 Git 基线与既有非发布字段
- **THEN** 控制面允许创建普通 CodeAgent Run，包括用户随后提出的 local 发布任务
- **AND** 不把历史 `local_publish_*` 列写入冻结契约

#### Scenario: 发布配置缺失

- **WHEN** Manifest 不含 local 发布命令或确认策略
- **THEN** 控制面不得因此拒绝创建 Run
- **AND** 不得要求用户在 Manifest UI 补齐发布字段
