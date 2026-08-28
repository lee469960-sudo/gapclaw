## ADDED Requirements

### Requirement: Agent 配置支持受授权的 Code Project 绑定
Agent 配置 API 和界面 SHALL 支持显式保存 `code` Profile 与一个 Code Project 绑定。系统 SHALL 仅返回当前主体有访问权且已启用的项目作为可选项，并在保存时再次校验授权与项目状态；未声明 Profile 的存量 Agent SHALL 继续按 Standard Profile 处理。

#### Scenario: 保存授权项目绑定
- **WHEN** 用户为 Agent 选择 `code` Profile 并提交一个有权访问且已启用的项目
- **THEN** 系统持久化该 Profile 与项目绑定
- **AND** 返回的 Agent 配置包含可展示的 Profile 和项目标识

#### Scenario: 保存未授权或停用项目
- **WHEN** 用户提交无权访问、已停用或不存在的项目作为 Code Profile 绑定
- **THEN** 系统拒绝保存并返回稳定的可行动原因
- **AND** 既有 Agent 配置保持不变

#### Scenario: 存量请求不受影响
- **WHEN** 存量客户端创建或更新 Agent 时未携带 Profile 或 Code Project 字段
- **THEN** 系统继续使用 Standard Profile 默认语义
- **AND** 不要求客户端补充 CodeAgent 配置
