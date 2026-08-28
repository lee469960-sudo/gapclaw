## Why

一次真实失败任务（充值用户数据导出）两轮复盘暴露了 7 类缺陷，共同指向「工具结果无法可靠进入下一轮推理」：

1. 工具结果被 6000 字符硬截断，字段映射从未被蒸馏进 PLAN，模型空转 200 轮。
2. 产出脚本混入 MiniMax 原生分隔符 `]<...>[` 与畸形闭合（`gen_batches.py]`），令牌泄漏污染可执行内容。
3. MCP 子任务过慢：逐 uid 往返、反复拉取工具目录。
4. 执行过程只见裸 `[shell]`/`[mcp_tool_call]`，看不到真实命令。
5. 上一轮工具结果未以原生配对（`role:tool` + `tool_call_id`）回填，模型「用上一轮结果前就进入下一轮」。
6. 大量 MCP SQL 调用未形成任何中间产物：仅「>6000 字符」的结果才落盘，普通结果不落盘、不缓存、不去重，随上下文裁剪丢失。
7. 重复语句耗尽 200 轮：模型失忆后反复执行相同 MCP 查询，进度不可见。

## What Changes

- **R1 截断可配置 + MCP 全量落盘 + 更大输出**：`tool_result_clip` 改为 Agent 可配置值（默认 6000，替代模块常量），`max_tokens` 4096→8192；**所有 MCP 结果一律落盘 + 入去重 cache（不再以「>6000」为门槛）**，摘要含顶层键列表 + 元素个数/行数；`tool_result_clip` 全透出（路由 `AgentBody` + 写逻辑 clamp + `startup.py` 迁移加列 + 前端表单）。
- **R2 令牌泄漏剥离**：泄漏令牌正则同时覆盖 ChatML `<|...|>` 与 MiniMax `]<...>[`；路径归一化剥离首尾方括号；解析前剥离泄漏令牌。
- **R3 MCP 提速**：工具目录 `tools/list` 跨 run 缓存（600s TTL）；系统提示加「取数效率」引导。
- **R4 步骤可见性**：工具步骤标题显示真实命令 + 参数预览；成功步骤也保留 content。
- **R5 role:tool 原生回填**：原生 `tool_calls` 响应的结果以 `role:tool` + 匹配 `tool_call_id` 回填；原生响应直接生成带 id 的 `ToolStep`；归一化链路透传 `tool_calls`/`tool_call_id`/`role:tool`；上下文成组裁剪。
- **R6 查询去重软提示**：记录已执行查询签名（`mcp_id + tool + 规范化参数`），重复命中时软提示「已缓存、结果路径、READ 复用」，不重复请求；无硬门禁。
- **R7 进度回显**：重复命中 / 重发 PLAN 时回显已落盘清单（`tool → path`），复用现有进度块与 coach hint，不新增机制。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `agent-runtime`: 新增截断可配置、令牌泄漏剥离、MCP 目录缓存与取数效率引导、步骤可见性、role:tool 原生回填五条需求。

## Impact

- `apps/api/app/models.py`（`tool_result_clip` 列）
- `apps/api/app/routers/agent.py`（`AgentBody` 声明 + 写逻辑 clamp）
- `apps/api/app/startup.py`（迁移加列）
- `apps/web/src/views/Agents.vue`（前端表单暴露）
- `apps/api/app/services/tool_parser.py`（令牌剥离、路径归一化、`ToolStep.tool_call_id`）
- `apps/api/app/services/llm_client.py`（`extract_chat_response_text` 剥离、原生步骤来源、`normalize_chat_messages`/`fit_messages_to_context` 透传与成组裁剪）
- `apps/api/app/services/mcp_client.py`（MCP 结果全量落盘、`query_cache` 去重 + 软提示回传 path）
- `apps/api/app/services/agent_runtime/runtime.py`（`max_tokens`、`tool_result_clip` 接线、原生回填、步骤标题、进度回显）
- `apps/api/app/services/agent_runtime/context_manager.py`（`push_assistant_native`、role:tool 结果推送、进度回显）
- `apps/api/app/services/agent_runtime/system_prompt.py`（取数效率引导）
- `apps/api/tests/`（新增单测）
