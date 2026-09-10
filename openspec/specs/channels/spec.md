# channels Specification

## Purpose

消息渠道入站处理契约：把各渠道发来的消息与附件解析为可交由 Agent 处理的输入，其中 Telegram `document` 附件需下载进 sandbox workspace 并注入消息上下文，供 Agent 通过 READ/SHELL 复用。

## Requirements

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

### Requirement: TG 附件发送

当 agent 的最终回复包含交付附件（`attach=` 标注解析出的路径，或兜底挑出的文件）且渠道为 TG 时，引擎 MUST 按路径经 `download_path` 规范化解析后调用 `send_document` 逐个发送；MUST 支持多附件；显式标注 MUST 优先于自动兜底。未标注 `attach` 时 MUST 保留现有「挑一个最新根目录 xlsx/csv 文件」的兜底逻辑。非 TG 渠道 MUST 行为不变。

#### Scenario: 显式标注多附件发送

- **WHEN** TG 回复包含 `attach=path1,path2`（或独立 `ATTACH:` 行）解析出的多个交付路径
- **THEN** 引擎按路径逐个 `send_document` 发出

#### Scenario: 显式优先 + 兜底

- **WHEN** 回复未标注 `attach`
- **THEN** 引擎保留现有「挑一个最新根目录 xlsx/csv 文件」逻辑作为兜底发送

#### Scenario: 非 TG 渠道行为不变

- **WHEN** 渠道非 TG（web 或其它未实现 `send_document` 的 IM）
- **THEN** 不发送附件，行为与现状一致

### Requirement: Telegram poll failures are structured and rate-aware
Telegram polling MUST log failures with structured non-secret context and observable retry or backoff state so operators can distinguish current instability from historical failures.

#### Scenario: Telegram poll failure includes safe context
- **WHEN** Telegram polling fails for a channel
- **THEN** the log event includes the channel identifier, failure class, exception type, and retry or backoff state without logging bot tokens

#### Scenario: Repeated Telegram poll failures are not unbounded noise
- **WHEN** the same Telegram channel continues failing with the same failure class
- **THEN** diagnostics expose the repeated failure count or interval without requiring one full traceback per polling attempt

#### Scenario: Successful poll after failures is observable
- **WHEN** Telegram polling succeeds after one or more failures
- **THEN** diagnostics expose that the failure streak recovered so recent health is distinguishable from all-time failure totals
