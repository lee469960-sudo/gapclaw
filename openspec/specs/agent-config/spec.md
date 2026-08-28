# agent-config Specification

## Purpose

Agent 属性面板的模型绑定配置：模型下拉框同时列出单模型与模型组，支持把 Agent 绑定到单模型或模型组，并保证列表与详情正确展示组名、默认模型优先单模型。

## Requirements

### Requirement: Agent 属性面板可绑定 LLM 组

Agent 属性面板的模型下拉框 MUST 同时列出单模型（`type:"llm"`）与模型组（`type:"group"`）；MUST 用分组视觉区分两者（单模型 / 模型组）；用户 MUST 能选择模型组作为 `llm_id` 并成功保存；绑定后 Agent 列表与详情 MUST 正确显示组名。默认模型 MUST 优先单模型。

#### Scenario: 下拉框包含组

- **WHEN** 加载 Agent 属性面板的模型下拉框
- **THEN** 下拉框同时包含 `type:"llm"` 与 `type:"group"` 的 LLM 资源
- **AND** 不再按 `type:"llm"` 过滤掉模型组

#### Scenario: 分组视觉区分

- **WHEN** 渲染模型下拉框
- **THEN** 单模型与模型组分组显示（`el-option-group`「单模型」/「模型组」）

#### Scenario: 绑定组可保存

- **WHEN** 用户选择某模型组并保存 Agent
- **THEN** 保存成功，Agent 的 `llm_id` 指向该模型组 id

#### Scenario: 组名展示

- **WHEN** 绑定了模型组的 Agent 出现在列表或详情
- **THEN** `llm_name` 显示该模型组的名称

#### Scenario: 默认优先单模型

- **WHEN** 新建 Agent 未显式选择模型
- **THEN** 默认模型为单模型（优先 `MinMax`，其次首个单模型），不默认选中模型组

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
