## ADDED Requirements

### Requirement: TG 入站 document 文件下载并交给 Agent

当 TG 收到含 `document` 的消息时，引擎 MUST 解析其 `file_id`、调用 `getFile` 获取 `file_path`、下载到 sandbox workspace 并在 `inbound.text` 追加「附件已下载：<path>」，使 Agent 能通过现有 `READ:`/`SHELL:` 处理该文件。纯 `document` 消息（无 `text`/`caption`）MUST 也触发 Agent，文件路径作为唯一输入。`photo`/`voice`/`video`/`audio` 附件 MUST 不下载、不处理、不报错，行为与现状一致。

#### Scenario: document 消息下载并注入路径

- **WHEN** TG 收到含 `document` 的消息
- **THEN** 引擎解析 `file_id` 并经 `getFile` 下载到 sandbox workspace
- **AND** `inbound.text` 追加「附件已下载：<path>」，Agent 可 `READ` 该路径

#### Scenario: 纯 document 无 caption 触发 Agent

- **WHEN** TG 收到仅含 `document`、无 `text`/`caption` 的消息
- **THEN** 引擎触发 Agent，文件路径作为唯一输入，不再静默丢弃

#### Scenario: 非 document 附件行为不变

- **WHEN** TG 收到 `photo`/`voice`/`video`/`audio` 附件
- **THEN** 引擎不下载、不处理、不报错，行为与现状一致

#### Scenario: 超过下载上限

- **WHEN** `document` 文件大小超过 20MB（Bot API `getFile` 上限）
- **THEN** 引擎报错并提示超限，不尝试下载
