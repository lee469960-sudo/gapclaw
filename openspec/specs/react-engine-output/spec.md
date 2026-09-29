# react-engine-output Specification

## Purpose

把 React-Engine 一次运行的结束结果收成统一信封，让用户只看到 Markdown 回复，并把未完成时的中间代码留在执行过程里。

## Requirements

### Requirement: 结束结果使用固定信封

引擎 MUST 在持久化用户可见回复之前，把该次运行的结束结果表示为仅含 `version`、`status`、`type`、`message`、`data`、`actions` 的对象。`version` MUST 为 `"1.0"`。系统 MUST NOT 增加或省略顶层字段。

#### Scenario: 正常完成的回复带齐六个字段

- **WHEN** 一次运行以完成状态结束并写入会话
- **THEN** 该回复的结构化字段包含且仅包含上述六个字段
- **AND** `version` 为 `"1.0"`

#### Scenario: 无工具的普通完成同样收口

- **WHEN** 运行没有调用 Skill、MCP 或其它工具，并以普通文本完成
- **THEN** 结束结果仍然是同一信封
- **AND** `status` 为 `ok`，`type` 为 `answer`，`data` 为 `{}`

### Requirement: 引擎决定状态与类型

引擎 MUST 填写 `status` 与 `type`，不得照抄模型自拟的信封。对应关系 MUST 为：有结构化结果的正常完成是 `ok`/`data`；没有结构化结果的正常完成是 `ok`/`answer`；预算用尽或用户停止是 `partial`/`answer`；运行停下并等待用户补充或授权是 `need_input`/`clarify`；执行失败是 `error`/`error`。第一版 MUST NOT 发出 `type=action`。

#### Scenario: 预算用尽

- **WHEN** 运行因达到轮次上限而结束，且没有被接受的最终回复
- **THEN** `status` 为 `partial`，`type` 为 `answer`
- **AND** `message` 说明已完成的部分和缺口

#### Scenario: 用户停止

- **WHEN** 用户在运行结束前请求停止
- **THEN** `status` 为 `partial`，`type` 为 `answer`
- **AND** `message` 说明已停止以及已经做到的步骤

#### Scenario: 等待用户

- **WHEN** 引擎因必须由用户补充信息或授权而停止
- **THEN** `status` 为 `need_input`，`type` 为 `clarify`

#### Scenario: 执行失败

- **WHEN** 运行因执行失败而结束
- **THEN** `status` 为 `error`，`type` 为 `error`

### Requirement: 用户只看到 message

会话正文、Web 主气泡、群聊和 IM 渠道 MUST 只展示 `message` 的 Markdown。完整信封 MUST 保存在同一条消息的结构化字段中。进行中的轮询可以携带信封，但 MUST NOT 用它替换主气泡，直到该次运行结束。执行过程卡片 MUST 继续使用现有步骤结构。

#### Scenario: 结束时主气泡是 Markdown

- **WHEN** 运行结束并产生用户可见回复
- **THEN** 主气泡渲染 `message` 的 Markdown
- **AND** 气泡正文不是原始 JSON 信封

#### Scenario: 进行中不刷新主气泡

- **WHEN** 运行仍在进行并产生轮询更新
- **THEN** 主气泡保持结束前的内容
- **AND** 执行过程卡片可以更新

#### Scenario: 渠道发送正文

- **WHEN** 已配置的 IM 渠道发送该次运行的最终回复
- **THEN** 渠道收到的文本是 `message`
- **AND** 渠道消息不包含原始信封 JSON

#### Scenario: 未配置渠道

- **WHEN** Agent 没有配置 IM 渠道
- **THEN** 会话仍保存 `message` 与信封
- **AND** 不因缺少渠道而失败或改写状态

### Requirement: message 不包含中间原文

`message` MUST 使用 Markdown，并且 MUST NOT 包含系统提示词、内部路由、推理过程、Skill/MCP/Agent 原始响应或未完成运行的半截代码。命令、SQL、JSON 和有意交付的代码 MUST 放在 Markdown 代码块中。未绑定 Skill 或 MCP 时，既有的人话停止说明 MUST 保留，并作为 `message`；该停止 MUST NOT 调用未授权资源。

#### Scenario: 未完成运行不把代码当回复

- **WHEN** 运行在产生大段代码或工具原文后以 `partial` 结束
- **THEN** `message` 说明进度和缺口
- **AND** 那段原始代码不出现在 `message` 中

#### Scenario: 未绑定资源保持可解释停止

- **WHEN** 用户要求使用当前 Agent 未绑定的 Skill 或 MCP
- **THEN** `message` 说明未绑定原因
- **AND** 系统不调用该未授权资源

### Requirement: data 只保存结构

`data` MUST 只包含可解析的结构，例如数量、字段、路径和状态。大段原文、代码和工具输出 MUST NOT 写入 `data`。没有这种结构时 `data` MUST 为 `{}`。`actions` MUST 为 `[]`，界面 MUST NOT 为此渲染操作按钮。

#### Scenario: 查询结果进入 data

- **WHEN** 运行正常完成，且结果可表示为计数字段
- **THEN** `type` 为 `data`
- **AND** `data` 含这些字段
- **AND** `message` 用 Markdown 表格或列表呈现同一结果

#### Scenario: 工具原文不进 data

- **WHEN** 工具返回大段无法解析为结构的文本
- **THEN** `data` 为 `{}` 或只含路径、状态等结构字段
- **AND** 原始文本不作为 `data` 的值保存

#### Scenario: 没有后续动作

- **WHEN** 任意运行结束
- **THEN** `actions` 为 `[]`
- **AND** 界面不出现由 `actions` 生成的按钮

### Requirement: 旧消息保持原样渲染

没有信封字段的既有助手消息 MUST 继续按现有正文做 Markdown 渲染。新写入 MUST NOT 改写这些历史正文。

#### Scenario: 打开旧会话

- **WHEN** 用户打开一条在本变更之前保存的助手消息
- **THEN** 界面渲染该消息的原有正文
- **AND** 不因为它缺少信封而显示错误或空白
