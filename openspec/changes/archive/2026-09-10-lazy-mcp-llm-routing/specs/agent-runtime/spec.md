## ADDED Requirements

### Requirement: MCP 候选资格基于可路由能力元数据

自动 MCP 路由 MUST 仅将当前 Agent 已绑定、调用权限有效、且具有非空能力描述或标签的 MCP 作为候选。缺少两者的已绑定 MCP MUST 保持可配置但 MUST 不参与自动路由，并向配置者呈现需要补齐能力描述的状态。

#### Scenario: 描述完整的已绑定 MCP 成为候选

- **WHEN** MCP 已绑定至 Agent、调用权限有效且具有非空 `description` 或 `tags`
- **THEN** 系统将其名称、标签和描述作为路由候选元数据

#### Scenario: 元数据缺失的 MCP 不参加自动路由

- **WHEN** 已绑定 MCP 的 `description` 与 `tags` 均为空
- **THEN** 系统不将其提交给自动路由
- **AND** 配置者可观察到需补齐能力描述的状态

### Requirement: 路由失败采用最小权限降级并受限补选

当初始路由或补选路由失败、超时、返回无效结果或无足够置信度时，运行时 MUST 将该次选择视为零个 MCP，MUST NOT 因此加载所有已绑定 MCP。主 Agent 仅在明确需要额外能力、已选 MCP 不可达或其目录缺少所需工具时请求补选；单次运行最多执行两次补选。补选 MUST 不重复加载已经选中的 MCP。

#### Scenario: 路由失败不回退全量目录

- **WHEN** 路由 LLM 超时或返回无法验证的 MCP 标识
- **THEN** 本次运行不加载任何未明确选中的 MCP
- **AND** 系统不调用未选 MCP 的 `tools/list`

#### Scenario: 缺少能力时受限补选

- **WHEN** 主 Agent 明确需要额外能力，或已选 MCP 不可达、未声明所需工具
- **THEN** 系统可以执行一次补选路由并仅加载新选中的合格 MCP
- **AND** 单次运行中的补选次数不超过两次

### Requirement: MCP 路由决策可审计且目录缓存不扩大访问范围

运行时 MUST 为每次路由和补选记录脱敏的结构化审计事件，至少含请求摘要、候选标识、选择结果、选择理由、触发原因、补选序号和加载结果。目录缓存 MUST 仅保存曾被实际选择的 MCP，并在 MCP 变更或到期后失效；缓存 MUST NOT 触发未选 MCP 的连接或目录发现。

#### Scenario: 初始选择产生脱敏审计事件

- **WHEN** 初始 MCP 路由完成
- **THEN** 系统记录不含完整提示词或凭据的结构化审计事件
- **AND** 事件含选择结果、理由和实际加载结果

#### Scenario: 未选 MCP 不因缓存而被访问

- **WHEN** 运行使用跨运行目录缓存
- **THEN** 仅先前实际选中过的 MCP 可以使用缓存
- **AND** 未被本次路由选择的 MCP 不连接、不执行目录发现

## MODIFIED Requirements

### Requirement: 多 MCP 绑定通过 LLM 语义路由惰性发现并正确分派工具调用

当 agent 绑定多个 MCP 时，运行时 MUST 在目录发现前使用 Agent 配置的 LLM，根据用户请求、当前运行上下文和合格候选 MCP 的名称、标签及描述，选择零个、一个或多个 MCP。路由输入 MUST NOT 包含候选 MCP 的完整工具目录。运行时 MUST 仅对选中的 MCP 连接并调用 `tools/list`，再将其目录提供给主 Agent；未选 MCP MUST 不连接、不调用 `tools/list`、不创建目录缓存。用户点名 MCP 时，运行时 MUST 将点名作为路由 LLM 的强信号，而非绕过路由。路由输出 MUST 在服务端验证为当前 Agent 已绑定且调用权限有效的 MCP。对于已发现目录中的工具调用，运行时 MUST 分派到实际声明该工具的已选 MCP，而非固定返回第一个绑定 MCP。

#### Scenario: 单域请求只加载语义匹配的 MCP

- **WHEN** Agent 同时绑定能力描述为 ClickHouse 性能优化与数据导出/查询的 MCP，且用户请求性能优化
- **THEN** 路由 LLM 可以仅选择前者
- **AND** 系统仅对前者执行连接和 `tools/list`
- **AND** 后者不出现在主 Agent 的工具目录中

#### Scenario: 跨域请求选择多个 MCP

- **WHEN** 用户请求同时需要性能分析和数据导出/查询
- **THEN** 路由 LLM 可以选择多个合格 MCP
- **AND** 系统仅发现这些被选择 MCP 的目录

#### Scenario: 用户点名 MCP 仍经路由与授权校验

- **WHEN** 用户在请求中点名一个当前 Agent 已绑定的 MCP
- **THEN** 系统将点名作为路由输入的强信号
- **AND** 仅在路由结果通过绑定和调用权限校验后加载该 MCP

#### Scenario: 匹配工具所在已选 MCP

- **WHEN** 路由选择多个 MCP，且目标工具仅存在于其中某一个已发现目录中
- **THEN** 该工具在它实际所在的 MCP 上执行
- **AND** 单 MCP 绑定的行为不变
