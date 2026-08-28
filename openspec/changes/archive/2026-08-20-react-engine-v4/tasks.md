## 1. 截断可配置 + MCP 全量落盘 + 更大输出（R1）

- [x] 1.1 `models.py` 新增 `tool_result_clip` 列（Integer，default 6000）+ `to_dict`
- [x] 1.2 `routers/agent.py` `AgentBody` 声明 `tool_result_clip: int = 6000` + 写逻辑 clamp `max(1, min(.., 100000))`
- [x] 1.3 `startup.py` 幂等 `ALTER TABLE agents ADD COLUMN tool_result_clip INTEGER DEFAULT 6000`
- [x] 1.4 `Agents.vue` 表单暴露 `tool_result_clip` 输入（对齐 `mcp_soft_circuit`）
- [x] 1.5 `runtime.py` 主循环 `max_tokens=8192`；`tool_result_clip` 接线到 `push_tool_result(clip=..)`
- [x] 1.6 `mcp_client.py` `_materialize` 去掉 `len(text) > large_result_chars` 门禁：所有非空 MCP 结果一律落盘 + 入 `query_cache`
- [x] 1.7 `mcp_client.py` 落盘摘要补「元素个数/行数」（`mcp_results` 条目 / `_cached_reference`）

## 2. 令牌泄漏剥离（R2）

- [x] 2.1 `tool_parser.py` `LEAKED_TOOL_TOKEN_RE` 覆盖 `<|...|>` 与 `]<...>[`
- [x] 2.2 `tool_parser.py` `_norm_path` 剥离首尾方括号
- [x] 2.3 `tool_parser.py` `extract_tool_steps` 解析前剥离泄漏令牌
- [x] 2.4 `llm_client.py` `extract_chat_response_text` 归一化原生 tool_calls 时剥离令牌

## 3. MCP 提速（R3）

- [x] 3.1 `utils.py` 工具目录 `tools/list` 跨 run 缓存（600s TTL）
- [x] 3.2 `system_prompt.py` 绑定 MCP 时追加「取数效率」引导（中性、不点名工具）

## 4. 步骤可见性（R4）

- [x] 4.1 `runtime.py` `_tool_step_title` 输出真实命令 + 参数预览
- [x] 4.2 `runtime.py` 成功步骤也保留 content（截断显示）

## 5. role:tool 原生回填（R5）

- [x] 5.1 `tool_parser.py` `ToolStep` 加 `tool_call_id` 字段
- [x] 5.2 `llm_client.py` 原生 `tool_calls` 直接生成带 id 的 `ToolStep`（单一来源，不二次文本解析）
- [x] 5.3 `llm_client.py` `normalize_chat_messages` 透传 assistant `tool_calls`、tool 消息 `tool_call_id`
- [x] 5.4 `llm_client.py` `fit_messages_to_context` 成组裁剪（assistant(tool_calls)+连续 role:tool 原子组）
- [x] 5.5 `context_manager.py` 新增 `push_assistant_native` + role:tool 结果推送
- [x] 5.6 `runtime.py` 循环按「响应是否含 tool_calls」分流回填形态

## 6. 查询去重软提示（R6）

- [x] 6.1 `mcp_client.py` 确认 `_dedup_key` 签名与「规范化参数」一致（键保留、值参与、忽略空白/键序）
- [x] 6.2 `mcp_client.py` 命中 cache 时软提示回传落盘 `path`（`_cached_reference`），供模型 READ 复用
- [x] 6.3 仅 MCP 参与去重；RAG/httpmcp 预留统一接口（注释/占位，不实现）

## 7. 进度回显（R7）

- [x] 7.1 `runtime.py` 重复命中 / 重发 `PLAN:` 时，把「已落盘清单」（`tool → path`）回显进 `progress_block`/coach hint
- [x] 7.2 复用 `add_progress`/`add_coach_hint`，不新增轮数计数器 / 阈值

## 8. 测试与回归

- [x] 8.1 新增 `tool_result_clip` 配置接线 + 路由 clamp 单测
- [x] 8.2 新增泄漏令牌剥离单测（`<|...|>` 与 `]<...>[`、`xxx.py]` 归一化）
- [x] 8.3 新增 `ToolStep.tool_call_id` + 原生步骤来源单测
- [x] 8.4 新增归一化透传 + 成组裁剪单测（不孤立 tool 消息）
- [x] 8.5 新增 MCP 全量落盘单测（结果 ≤6000 也落盘 + 入 cache）
- [x] 8.6 新增查询去重软提示单测（相同签名命中、不同参数不命中）
- [x] 8.7 新增进度回显单测（重复命中/重发 PLAN 回显清单）
- [x] 8.8 全量测试回归绿；无新增静态门禁 / 硬中断
