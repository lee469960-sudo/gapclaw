## Purpose

Agent 属性面板的模型绑定配置：模型下拉框同时列出单模型与模型组，支持把 Agent 绑定到单模型或模型组，并保证列表与详情正确展示组名、默认模型优先单模型。

## ADDED Requirements

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
