## ADDED Requirements

### Requirement: 运行结束的用户可见回复必须经过统一输出信封

一次 Agent 运行结束并产生用户可见回复时，运行时 MUST 先形成 `react-engine-output` 所定义的信封，再把其中的 `message` 作为会话正文。预算用尽和用户取消的可见回复 MUST 使用 `partial`，不得只返回「已停止」，也不得把当时的中间代码或工具原文当作正文。等待用户补充或授权 MUST 使用 `need_input`。执行过程卡片的步骤结构、工具协议和 `FINAL` 语法 MUST 保持不变。

#### Scenario: 用户取消后的可见回复

- **WHEN** 用户在 ReAct 运行结束前取消
- **THEN** 会话中的助手正文是说明已停止和已有进展的 Markdown
- **AND** 该回复的结构化状态为 `partial`
- **AND** 正文不是半截代码或工具原文

#### Scenario: 预算耗尽后的可见回复

- **WHEN** 运行达到轮次上限且没有被接受的最终回复
- **THEN** 会话正文说明已完成部分和缺口
- **AND** 结构化状态为 `partial`
- **AND** 运行状态持久化行为保持不变

#### Scenario: 执行过程不受信封替换

- **WHEN** 运行期间产生工具步骤
- **THEN** 执行过程卡片仍按现有步骤展示
- **AND** 这些步骤不被替换成统一输出信封
