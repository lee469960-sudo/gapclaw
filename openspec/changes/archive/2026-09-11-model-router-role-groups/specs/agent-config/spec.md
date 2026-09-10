## ADDED Requirements

### Requirement: Agent 配置支持路由策略和角色模型组管理

系统 SHALL 在 LLM/Agent 管理界面及 API 中提供单模型能力、角色模型组和路由策略的配置与展示。Agent SHALL 可显式选择一个已授权路由策略；未选择路由策略的既有 Agent MUST 保持现有 `llm_id` 直接绑定语义。

#### Scenario: 管理者配置并绑定路由策略

- **WHEN** 管理者配置有效 Router LLM、角色模型组映射和默认角色，并将该策略绑定至 Agent
- **THEN** 系统持久化该策略引用并在 Agent 详情展示策略名称

#### Scenario: 存量 Agent 保持直接绑定

- **WHEN** 存量 Agent 没有路由策略引用
- **THEN** 系统继续使用其现有 `llm_id` 运行
- **AND** 不要求配置能力元数据或迁移为角色模型组

### Requirement: 配置界面展示路由候选资格和兼容性

系统 SHALL 在单模型和角色模型组配置界面展示模型是否具备自动路由资格，以及不符合角色、运行时或模态要求的具体原因。界面 MUST 将兼容型 LLM Group 与角色模型组视觉区分。

#### Scenario: 不合格模型显示修复原因

- **WHEN** 模型缺少角色、运行时或模态能力而不能加入当前角色组
- **THEN** 界面显示该模型不可选及其需要补齐的能力

