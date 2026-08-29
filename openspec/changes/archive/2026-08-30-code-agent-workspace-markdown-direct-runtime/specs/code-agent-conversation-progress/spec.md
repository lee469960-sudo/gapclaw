## ADDED Requirements

### Requirement: CodeAgent 对话默认直接使用 Code Runtime

当 Agent profile 为 CodeAgent 且绑定 Claude Code runtime 时，系统 SHALL 将每条非空用户消息直接作为当前 Code Run 的任务目标提交给 Code Runtime，不得要求用户添加“开始执行:”或“开始实现”等触发词。历史前缀 MAY 作为兼容别名保留，但不得成为必需条件。

#### Scenario: 普通消息直接启动 Code Run

- **WHEN** CodeAgent 用户发送普通的非空任务描述
- **THEN** 系统创建并排队一个 CodeAgent Run
- **AND** 该消息内容作为任务目标传入 Claude Code runtime
- **AND** 对话展示正常的运行过程和终态结果

#### Scenario: 旧前缀仍可兼容

- **WHEN** 用户发送以“开始执行:”开头的任务
- **THEN** 系统可去除兼容前缀后创建 Code Run
- **AND** 不要求用户必须使用该前缀

#### Scenario: 空消息被拒绝

- **WHEN** CodeAgent 用户发送空白消息或仅包含兼容前缀的消息
- **THEN** 系统不创建 Code Run
- **AND** 返回缺少任务目标的明确提示
