## ADDED Requirements

### Requirement: Agent 配置可显式声明运行 Profile
Agent 配置 SHALL 支持可选 Profile 声明，至少包括 `standard` 和 `code`。未声明 Profile 的存量与新建 Agent SHALL 默认使用 `standard`，且其现有配置语义保持不变。

#### Scenario: 存量 Agent 未声明 Profile
- **WHEN** 加载或运行一个未包含 Profile 字段的存量 Agent
- **THEN** 系统将其视为 `standard` Profile
- **AND** 不要求迁移用户配置才能继续运行

#### Scenario: 配置 Code Profile
- **WHEN** 有权限的用户为 Agent 显式选择 `code` Profile 并保存
- **THEN** 系统持久化该显式选择
- **AND** 后续任务可按项目策略使用 Code Profile

### Requirement: Code Profile 配置必须受权限与项目策略约束
系统 SHALL 仅允许具有相应权限且关联受管 Code 项目的主体选择或使用 `code` Profile。仅保存 `code` Profile 配置 SHALL 不自动授予仓库、网络、秘密、Git 写入或工具权限。

#### Scenario: 未授权主体选择 Code Profile
- **WHEN** 不具备 Code Profile 使用权限的主体尝试保存或运行 `code` Profile
- **THEN** 系统拒绝该操作并记录授权失败

#### Scenario: Code Profile 没有项目策略
- **WHEN** 已配置 `code` Profile 的 Agent 尝试在没有有效项目 Manifest 的上下文中启动修改任务
- **THEN** 系统拒绝进入可写执行阶段
