# Progress — react-engine-v8

记录实际完成情况。勾选 `tasks.md` 前的落地依据（代码 + 测试 + 回归）。

## 状态总览

- **tasks.md 进度**：23 / 23（5 组，全部完成）
- **阶段**：A–E 全部完成
- **回归**：`python -m pytest tests/ -q` → **205 passed**（190 存量 + 15 新增 v8）

## 完成记录

### 阶段 A — R1 对话路由（1.1–1.2）

- **[1.1]** `runtime.py::_is_conversational` 改为：无 mcp/rag/skill/httpmcp 绑定 且 `allowed_actions ∩ _TOOL_ACTIONS == ∅`（`_TOOL_ACTIONS = {shell, file_read, file_write, file_search_replace, file_search, recall}`）才走对话路径。依据：`test_is_conversational_*`（5 条）绿。
- **[1.2]** `run_agent` 加载 `httpmcps`（`json.loads(getattr(agent,"httpmcps",None) or "[]")`）→ `from_params(httpmcp_ids=…)`，`AgentContext.httpmcp_ids` 字段已加。依据：测试 `test_is_conversational_resource_binding_goes_modular` 覆盖 httpmcp 绑定。

### 阶段 B — R2 httpmcp_call（2.1–2.8）

- **[2.1]** `models.py` `Agent.httpmcps`（Text 默认 `"[]"`）+ `to_dict` 透出 `"httpmcps"`；`startup.py` 加 `ALTER TABLE agents ADD COLUMN httpmcps TEXT DEFAULT '[]'`（见 findings F1）。
- **[2.2]** `agent.py` `DEFAULT_ACTIONS` 加 `"httpmcp_call"`（`KNOWN_ACTIONS` 随之包含）。
- **[2.3]** `system_prompt.py::build_tools_desc` 加 `httpmcp_ids` 参数，绑定 httpmcp 时展平 `_tools()`（name+desc+args，cap 30）为 `HTTPMCP:` 目录；`build_tool_schemas` 加 `httpmcp_call`。
- **[2.4]** `tool_parser.py` `PROTOCOL_MARKERS` + 解析模式 + `clean_display_text` 加 `HTTPMCP:`。
- **[2.5]** `llm_client.py::_tool_call_to_step` 加 `httpmcp_call` 原生分支（`HTTPMCP: {tool} {norm}`）。
- **[2.6]** `agent_tools.py::execute_action` 加 `httpmcp_call` 分支：接收 `httpmcp_ids`，按工具名解析绑定 HttpMcp → `call_httpmcp(target, {**args, "tool": tool})`；未绑定/未知工具降级。依据：`test_httpmcp_call_*`（4 条）绿。
- **[2.7]** `runtime.py::run_agent` 绑定 httpmcp 时 auto-append `httpmcp_call`（镜像 mcp_tool_call）；`_run_modular` 把 `httpmcp_ids` 传给 `build_tools_desc` 与 `execute_action`。
- **[2.8]** `context_manager.py::push_tool_result` label_map 加 `"httpmcp_call": "HTTP 请求代理结果"`。

### 阶段 C — R3 file_search（3.1–3.7）

- **[3.1]** `models.py` `allowed_actions` 默认值加 `"file_search"`。
- **[3.2]** `agent.py` `DEFAULT_ACTIONS` 加 `"file_search"`（`KNOWN_ACTIONS` 随之包含，见 findings F2）。
- **[3.3]** `system_prompt.py` `build_tools_desc` 加 `- file_search: SEARCH: <query>…`；`build_tool_schemas` 加 `file_search`。
- **[3.4]** `tool_parser.py` 加 `SEARCH:`（同 2.4 机制）。
- **[3.5]** `llm_client.py` 加 `file_search` 原生分支（`SEARCH: {q}`）。
- **[3.6]** `agent_tools.py` 加 `file_search` 分支 + `_search_workplace`（递归 workplace、子串匹配、cap 200/6000、跳过隐藏目录与二进制，见 findings F4）。依据：`test_search_workplace_*`（3 条）绿。
- **[3.7]** `context_manager.py` label_map 加 `"file_search": "文件搜索结果"`。

### 阶段 D — 前端（4.1）

- **[4.1]** `Agents.vue` 加 HttpMcp 选择器（`Connection` 图标 + `type="warning"` 标签）+ `httpmcps` 表单字段 + picker 弹窗，镜像 mcps/rags/skills；后端 plumbing 见 findings F1。

### 阶段 E — 测试与回归（5.1–5.5）

- **[5.1–5.4]** 新增 `tests/test_react_engine_v8.py`（15 条）：`_is_conversational` 组合、`HTTPMCP:`/`SEARCH:` 解析 + 调用、`_search_workplace` 命中/无匹配/二进制、`build_tool_schemas` 含/gate `httpmcp_call`+`file_search`。
- **[5.5]** 全量回归 `python -m pytest tests/ -q` → **205 passed**；未新增循环门禁或静态阈值。
