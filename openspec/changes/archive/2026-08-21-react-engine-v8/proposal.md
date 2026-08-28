## Why

两个「勾选没生效」类 bug，根因已坐实（见 `docs/exploration/react-engine-v8.md`）：

1. **shell 误路由**：`_is_conversational`（`runtime.py:266-272`）只判 `mcp_ids/rag_ids/skill_ids`，完全忽略 `allowed_actions`。shell-only agent（无资源绑定但 `allowed_actions` 含 `shell`）被误判为「无工具对话」，走 `_run_conversational`（单轮、无工具），而 `build_conversational_system` 明确禁止 `SHELL:`，shell 权限从未生效。
2. **死勾选项**：前端 `Agents.vue` 暴露 7 个动作勾选项，其中 `httpmcp_call`（HTTP 请求代理）、`file_search`（搜索文件内容）两个在后端**没有对应动作**（后端全库 `grep -w` 均 0 命中），提交后被 `_normalize_allowed_actions`（`agent.py:63-67`）静默丢弃。

## What Changes

- **R1 对话路由尊重工具动作**：`_is_conversational` 扩展为「无资源绑定 **且** `allowed_actions` 无任何工具动作」才走对话路径；shell-only agent 改走 `_run_modular` 工具循环。
- **R2 `httpmcp_call` 真实动作**：把独立资源 `HttpMcp` 接进 ReAct 循环——`Agent` 新增 `httpmcps` 绑定字段 + 前端选择器，协议 `HTTPMCP: <工具名> {json vars}`（镜像 MCP 展平解析），执行落到 `call_httpmcp`。
- **R3 `file_search` 真实动作**：新增内容 grep 动作，`SEARCH: <query>` 递归 workplace 返回 `file:line: 片段`。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `agent-runtime`: 新增「对话路由尊重工具动作」「httpmcp_call 动作」「file_search 动作」三条需求。

## Impact

- `apps/api/app/models.py`（`Agent.httpmcps` 字段 + `allowed_actions` 默认值加 `file_search`）
- `apps/api/app/routers/agent.py`（`DEFAULT_ACTIONS`/`KNOWN_ACTIONS` 加 `httpmcp_call`、`file_search`）
- `apps/api/app/services/agent_runtime/runtime.py`（`_is_conversational` 判定；加载/传参 `httpmcp_ids`）
- `apps/api/app/services/agent_runtime/system_prompt.py`（`HTTPMCP:`/`SEARCH:` 提示词 + `build_tool_schemas` 两 schema）
- `apps/api/app/services/tool_parser.py`（`HTTPMCP:`/`SEARCH:` regex）
- `apps/api/app/services/llm_client.py`（`httpmcp_call`/`file_search` 原生分支）
- `apps/api/app/services/agent_tools.py`（`execute_action` 两分支 + `_search_workplace`）
- `apps/api/app/services/agent_runtime/context_manager.py`（结果标签）
- `apps/web/src/views/Agents.vue`（HttpMcp 选择器 + `httpmcps` 表单字段）
- `apps/api/tests/`（新增单测）
