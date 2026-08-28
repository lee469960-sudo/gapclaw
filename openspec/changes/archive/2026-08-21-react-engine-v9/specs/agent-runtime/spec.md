## ADDED Requirements

### Requirement: 无依赖同轮批量引导

当构建系统提示 / 工具目录 / coach hints 时，引擎 MUST 引导模型「无依赖的独立步骤可同轮输出多个工具调用」（多个 READ/SEARCH、多个独立 SHELL 等），而非「一次一个工具」；有依赖的步骤（后步需前步结果）MUST 仍分轮。引擎 MUST 不新增并行 / 聚合机制——同轮多工具由既有 `for step in tool_steps` 串行执行。

#### Scenario: 同轮批量引导

- **WHEN** 构建系统提示 / 工具目录 / coach hints
- **THEN** 措辞包含「无依赖的独立步骤可同轮输出多个工具调用」
- **AND** 不含「一次只输出一个工具」等相反引导

#### Scenario: 有依赖仍分轮

- **WHEN** 后续步骤依赖前一步骤的结果
- **THEN** 引导仍分轮，不鼓励依赖链同轮

#### Scenario: 不新增引擎机制

- **WHEN** 模型同轮输出多个工具调用
- **THEN** 引擎按既有 `for step in tool_steps` 串行执行，不新增并行 / 聚合逻辑

### Requirement: 大结果落盘回显

当 READ/SHELL 工具结果超过阈值（默认 4000 字符）时，引擎 MUST 将全文落盘到 `task/<ts>/`，上下文仅回显「路径 + 前 N 行预览 + 总长度」；模型 MUST 能按需用 READ/SEARCH 取回片段。SEARCH 结果已 cap N 条，MUST 不落盘。未超阈值 MUST 保持现状（原文入上下文）。

#### Scenario: 超阈值落盘

- **WHEN** READ/SHELL 结果字符数超过阈值（默认 4000）
- **THEN** 全文落盘到 `task/<ts>/`，上下文仅回显「路径 + 前 N 行预览 + 总长度」

#### Scenario: 未超阈值原文入上下文

- **WHEN** READ/SHELL 结果字符数未超过阈值
- **THEN** 结果原文入上下文（现状不变）

#### Scenario: SEARCH 不落盘

- **WHEN** 动作是 SEARCH
- **THEN** 结果不落盘，保持 cap N 条的现状

#### Scenario: 按需取回

- **WHEN** 模型需要落盘文件的内容
- **THEN** 模型可用 READ/SEARCH 按需取回片段

### Requirement: token 估算安全余量

`fit_messages_to_context` 计算 `allowed_out` 时 MUST 在现有 `ctx - used - reserve` 基础上追加安全余量，以弥补 `estimate_tokens`（≈ `len/2`）对 shell 输出 / 代码类内容的低估，避免 input+output 仍超窗触发 MiniMax 2013。

#### Scenario: 追加安全余量

- **WHEN** `fit_messages_to_context` 计算 `allowed_out`
- **THEN** 结果在现有封顶基础上再扣安全余量

#### Scenario: 长任务不超窗

- **WHEN** 多轮 shell/READ 的长任务运行
- **THEN** 不再触发 MiniMax 2013（input+output 稳定在窗口内）
