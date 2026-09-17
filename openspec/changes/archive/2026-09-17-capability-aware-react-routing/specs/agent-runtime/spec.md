## ADDED Requirements

### Requirement: 入口路由可使用绑定能力元数据

Agent Runtime MUST 在确定 `chat`、`task` 或 `tool` 模式时允许使用当前 Agent 已绑定 MCP 的名称、标签和描述作为能力提示。该提示 MUST 是脱敏的元数据快照，不得包含完整工具目录、凭据或触发 MCP 连接；仅被选择进入任务路径后才能执行现有 MCP 惰性发现。

#### Scenario: 隐含查询命中绑定能力

- **WHEN** Agent 绑定的 MCP 描述包含交易账户、持仓和余额，用户请求“帮我看看当前持仓”
- **THEN** Runtime 将请求路由到 `task` 或 `tool`
- **AND** 在模式判定阶段不连接 MCP 或调用 `tools/list`

#### Scenario: 无能力匹配的普通概念问答

- **WHEN** 用户仅解释一个与绑定 MCP 能力词重合的概念，且没有外部查询意图
- **THEN** Runtime 保持 `chat` 快速路径

### Requirement: 未绑定资源请求必须可解释地停止

当用户显式点名当前 Agent 未绑定的 MCP/工具时，Runtime MUST 进入任务路径并返回明确的未绑定原因。该停止 MUST 不调用未授权资源、不得伪造工具结果，并 MUST 记录可审计的任务模式终止原因。

#### Scenario: 未绑定 okx-trader

- **WHEN** 用户要求使用 `okx-trader`，但该 MCP 不在当前 Agent 的绑定集合中
- **THEN** Runtime 返回未绑定提示
- **AND** 不建立 MCP 连接、不调用工具、不产生模拟持仓结果
- **AND** 终态指标包含任务模式和未绑定停止原因
