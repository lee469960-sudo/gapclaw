## 1. R1 — 对话路由尊重工具动作（agent-runtime）

- [x] 1.1 `runtime.py` `_is_conversational`：扩展为「无资源绑定 且 `allowed_actions` 无工具动作」判定（工具动作 = shell/file_read/file_write/file_search_replace/file_search/recall；httpmcp 纳入资源绑定判定）
- [x] 1.2 `runtime.py` 加载 `httpmcps` → `ctx.httpmcp_ids`（镜像 mcps）

## 2. R2 — httpmcp_call 真实动作（agent-runtime）

- [x] 2.1 `models.py` `Agent.httpmcps` 字段 + `to_dict` 透出
- [x] 2.2 `agent.py` `DEFAULT_ACTIONS`/`KNOWN_ACTIONS` 加 `httpmcp_call`
- [x] 2.3 `system_prompt.py`：绑定 httpmcp 时展平 `_tools()` 为目录（name+desc+args）+ `HTTPMCP:` 提示词；`build_tool_schemas` 加 `httpmcp_call`
- [x] 2.4 `tool_parser.py` 加 `HTTPMCP:` regex → `httpmcp_call`
- [x] 2.5 `llm_client.py` 加 `httpmcp_call` 原生分支
- [x] 2.6 `agent_tools.py` `execute_action` 加 `httpmcp_call` 分支（接收 `httpmcp_ids`，按工具名解析 → `call_httpmcp`）
- [x] 2.7 `runtime.py`：绑定 httpmcp 时 auto-append `httpmcp_call`（镜像 mcp_tool_call）+ 传 `httpmcp_ids` 给 execute_action
- [x] 2.8 `context_manager.py` 加 `httpmcp_call` 结果标签

## 3. R3 — file_search 真实动作（agent-runtime）

- [x] 3.1 `models.py` `allowed_actions` 默认值加 `file_search`
- [x] 3.2 `agent.py` `DEFAULT_ACTIONS`/`KNOWN_ACTIONS` 加 `file_search`
- [x] 3.3 `system_prompt.py` 加 `SEARCH:` 提示词 + `build_tool_schemas` 加 `file_search`
- [x] 3.4 `tool_parser.py` 加 `SEARCH:` regex → `file_search`
- [x] 3.5 `llm_client.py` 加 `file_search` 原生分支
- [x] 3.6 `agent_tools.py` `execute_action` 加 `file_search` 分支 + 新增 `_search_workplace`（递归、子串匹配、cap N、跳过二进制）
- [x] 3.7 `context_manager.py` 加 `file_search` 结果标签

## 4. 前端

- [x] 4.1 `Agents.vue` 加 HttpMcp 选择器 + `httpmcps` 表单字段（镜像 mcps/rags/skills picker）

## 5. 测试与回归

- [x] 5.1 `_is_conversational` 单测（资源绑定 × 动作集合 组合断言）
- [x] 5.2 `HTTPMCP:` 解析 + 按工具名调用单测（绑定/未绑定/无权限）
- [x] 5.3 `SEARCH:` 解析 + `_search_workplace` 单测（命中/无匹配/二进制跳过）
- [x] 5.4 `build_tool_schemas` 含 `httpmcp_call`/`file_search` 断言
- [x] 5.5 全量回归绿；无新增循环门禁 / 静态阈值
