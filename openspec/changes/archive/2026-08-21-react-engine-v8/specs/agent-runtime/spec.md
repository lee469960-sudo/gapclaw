## ADDED Requirements

### Requirement: 对话路由尊重 allowed_actions 工具动作

`_is_conversational` MUST 仅在「无 MCP/RAG/skill/httpmcp 资源绑定 **且** `allowed_actions` 不含任何非资源绑定工具动作（shell/file_read/file_write/file_search_replace/file_search/recall）」时判定为对话路径；否则 MUST 走 `_run_modular` 工具循环。

#### Scenario: shell-only agent 走工具循环

- **WHEN** agent 无 MCP/RAG/skill/httpmcp 绑定，`allowed_actions` 含 `shell`
- **THEN** `_is_conversational` 返回 False
- **AND** 走 `_run_modular`，`SHELL:` 被正常执行（或按 `agent_tools` 权限拒绝），不再产生对话回复

#### Scenario: 纯对话 agent 走对话路径

- **WHEN** agent 无资源绑定，且 `allowed_actions` 不含任何非资源绑定工具动作
- **THEN** `_is_conversational` 返回 True，走 `_run_conversational`

#### Scenario: 资源绑定 agent 走工具循环

- **WHEN** agent 绑定 MCP/RAG/skill/httpmcp 任一资源
- **THEN** `_is_conversational` 返回 False（现状不变）

### Requirement: httpmcp_call 动作

当 agent 绑定 httpmcp 资源且 `allowed_actions` 含 `httpmcp_call` 时，引擎 MUST 把绑定 HttpMcp 的 `_tools()` 展平为目录（name+desc+args），并支持协议 `HTTPMCP: <工具名> {json vars}` 与原生 schema `httpmcp_call(tool_name, arguments)`；MUST 按工具名解析到对应 HttpMcp 后调用 `call_httpmcp`。未绑定 httpmcp 时 MUST 返回降级提示而非静默失败；未开启 `httpmcp_call` 权限时 MUST 不声明该动作。

#### Scenario: 展平目录透出

- **WHEN** agent 绑定至少一个 HttpMcp 且 `allowed_actions` 含 `httpmcp_call`
- **THEN** 系统提示/工具目录包含绑定 HttpMcp 的 tools（name+desc+args）

#### Scenario: 按工具名调用

- **WHEN** 模型输出 `HTTPMCP: <工具名> {json vars}`（或原生 `httpmcp_call`）
- **THEN** 引擎按工具名定位到对应 HttpMcp，调用 `call_httpmcp(hm, {**vars, "tool": 工具名})`

#### Scenario: 未绑定降级

- **WHEN** agent 未绑定任何 HttpMcp 却尝试 `httpmcp_call`
- **THEN** 引擎返回「no http mcp configured」类提示，不抛异常

#### Scenario: 权限门控

- **WHEN** agent 绑定 HttpMcp 但 `allowed_actions` 不含 `httpmcp_call`
- **THEN** 系统提示/`build_tool_schemas` 不声明 `httpmcp_call`

### Requirement: file_search 动作

当 `allowed_actions` 含 `file_search` 时，引擎 MUST 支持协议 `SEARCH: <query>` 与原生 schema `file_search(query)`，递归 `workplace_root` 对文本文件做子串匹配（大小写不敏感），返回 `file:line: 片段`（cap N 条 + 总长度截断），跳过二进制/隐藏目录。未开启 `file_search` 权限时 MUST 不声明该动作。

#### Scenario: 内容命中

- **WHEN** 模型输出 `SEARCH: <query>`（或原生 `file_search`）且 workplace 存在匹配文本
- **THEN** 引擎返回 `file:line: 片段` 列表

#### Scenario: 无匹配

- **WHEN** `SEARCH:` 的 query 无任何匹配
- **THEN** 引擎返回空结果提示，不报错

#### Scenario: 权限门控

- **WHEN** `allowed_actions` 不含 `file_search`
- **THEN** 系统提示/`build_tool_schemas` 不声明 `file_search`
