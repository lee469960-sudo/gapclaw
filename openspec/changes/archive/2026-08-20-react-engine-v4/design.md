# Design — react-engine-v4

## Context

动机见 `proposal.md`「Why」。相关现状与约束：

- **铁律**：无任何静态硬门禁——循环只由 FINAL / 用户取消 / LLM 错误 / `max_iters` 结束；所有纠正走软提示；删除冗余代码优先。V4 不引入任何新计数器 / 阈值 / 硬停（`tool_result_clip` 是配置值、`tools/list` TTL 是缓存失效、路由 clamp 是输入校验，均非循环门禁）。
- 工具结果回填现状：`context_manager.push_tool_result` 用 `role:user` 文本回填；`fit_messages_to_context` 归一化时只保留 `role + content`，丢弃 `tool_calls`/`tool_call_id`。
- **第二轮根因收敛**：`McpSessionManager._materialize` 只在 `len(text) > 6000` 时才落盘并填充 `query_cache`（`_dedup_key = mcp_id + tool + json.dumps(args, sort_keys=True)`，命中返回「已缓存勿重复」）。普通大小的 MCP SQL 结果因此不落盘、不缓存、不去重，随上下文裁剪丢失 → 重复查询 → 200 轮耗尽。去掉该门禁即同时兑现 R1 全量落盘 / R6 去重 / R7 进度回显。
- 原生 function-calling 已在线（provider ∈ openai/minimax/deepseek 时 `tools=tool_schemas`），`extract_chat_response_text` 将 `tool_calls` 归一化为文本协议再交 `extract_tool_steps` 二次解析，`tool_call_id` 在此丢失。
- MiniMax（已查证）支持 `role:tool` + `tool_call_id`（OpenAI 兼容），多轮要求完整回填 `tool_calls`；严格校验：assistant 带 tool_calls 时 content 非 null、不 emit `strict`/`tool_choice`、单条 system。现状已满足这三条（`build_tool_schemas` 不 emit `strict`/`tool_choice`、content 不会为 null、system 已合并）。
- 已有软提示聚合 `add_coach_hint`/`flush_coach_hints`，本轮软提示继续复用它。

## Goals / Non-Goals

**Goals:**
- 工具结果截断上限可经 Agent 配置，模型单轮输出更大。
- 泄漏令牌不再进入可执行内容 / 落盘脚本。
- MCP 目录缓存 + 取数效率引导。
- 步骤可见真实命令。
- 原生 tool_calls 的结果以 role:tool + tool_call_id 回填，归一化链路不丢失配对。

**Non-Goals:**
- 不新增任何循环中断 / 强制阈值 / 计数器导致的 return/break（铁律）。
- 不改 MCP 源端（外部 MCP 返回什么不是引擎可控）。
- 不自动帮模型蒸馏 / 取数——软提示 only。
- 不新增协议关键字。

## Decisions

### D1: `tool_result_clip` 走 Agent 配置，替代模块常量（Q4=A）

完全对齐 `mcp_soft_circuit` 的接线：模型列（Integer，default 6000）+ `AgentBody` 声明 + 写逻辑 clamp `max(1, min(.., 100000))` + `startup.py` 幂等 `ALTER TABLE` + 前端 `Agents.vue` 表单。
- **为何**：R1 验收口径「Agent 可配置」；缺透出等于没做。

### D2: `max_tokens = 8192`

主循环 LLM 调用单轮输出上限 4096→8192。
- **为何**：字段映射 / 聚合结果更大，避免输出被 `max_tokens` 二次截断（截断点从上下文转移到输出上限）。

### D3: 泄漏令牌正则同时匹配 `<|...|>` 与 `]<...>[`

`LEAKED_TOOL_TOKEN_RE` 扩展；`_norm_path` 剥离首尾方括号；`extract_tool_steps` / `extract_chat_response_text` 在解析前剥离。
- **为何**：MiniMax 原生分隔符 `]<...>[` 是本次畸形脚本的直接来源；文件名 `]` 是闭合污染的伴生表现。

### D4: 工具目录 `tools/list` 跨 run 缓存（600s TTL）

- **为何**：目录拉取是每次运行的前置成本；跨 run 缓存去掉重复握手。TTL 是缓存失效，非循环门禁。

### D5: 取数效率软提示

绑定 MCP 时系统提示追加「取数效率」引导：批量数据优先日期区间 / 聚合谓词一次取全、不逐 uid query；落盘结果用 SHELL+pandas 后处理。
- **为何**：把「避免逐 uid 往返」变成显式引导，软提示 only，中性不点名工具。

### D6: 步骤标题显示真实命令 + 成功步骤存 content

`_tool_step_title` 输出 `[action] <真实命令/工具 + 参数预览>`；成功步骤也写 content（截断）。
- **为何**：UI 只见裸标签是本次可观测性痛点。

### D7: 双模式回填分流（Q1=A）

原生 `tool_calls` 响应 → 结果以 `role:tool` + 匹配 `tool_call_id` 回填；文本协议响应 → `role:user`。按「响应是否含 tool_calls」分流，单一响应形态一致。
- **为何**：保留原生结构化参数优势（直接压低 R2 畸形脚本概率）；文本路径行为不变。

### D8: 原生步骤单一来源（Q2=A）

原生响应直接由 `tool_calls` 生成 `ToolStep`（每个 call → 一个 step，`tool_call_id` 带上），不再二次文本解析；文本响应才走 `extract_tool_steps`。
- **为何**：避免「归一化文本再按顺序回填 id」的错位（多 call / call 与文本交错时不可靠）。

### D9: 归一化链路透传 + 成组裁剪（Q3=A）

`normalize_chat_messages` 保留 assistant 的 `tool_calls`、tool 消息的 `tool_call_id`；`fit_messages_to_context` 裁剪时把 `assistant(tool_calls)` + 其后连续 `role:tool` 作为原子组整组删。
- **为何**：不破坏原生配对，避免 provider 400。

### D10: MCP 结果全量落盘（去 >6000 门禁）

`_materialize` 不再以 `len(text) > large_result_chars` 为落盘条件——所有非空 MCP 查询结果一律写 `task/<run_ts>/mcp_result_N.json` 并入 `query_cache`；上下文的 `tool_result_clip` 截断不变（只影响展示）。
- **为何**：普通大小的结果是本轮「无中间产物 + 重复查询」的直接根因；去门禁后 R6/R7 的既有机制对全部结果生效。

### D11: 查询签名去重 + 软提示

复用 `query_cache` 的 `_dedup_key`（`mcp_id + tool + 规范化参数`）作签名；命中时软提示「已缓存、结果在 path、READ 复用」而非重复请求。仅 MCP；RAG/httpmcp 预留统一接口。
- **为何**：签名已存在，缺口是「静默命中」未告知模型可复用；软提示不引入硬门禁。

### D12: 进度回显（复用现有，不新增机制）

重复命中 / 重发 `PLAN:` 时，把「已落盘清单」（`tool → path`）回显进 `progress_block`/coach hint。复用 `add_progress`/`add_coach_hint`，不新增轮数计数器。
- **为何**：进度可见性直接缓解「每轮空转」；不新增门禁。

### D13: 落盘摘要含元素个数

摘要 = 顶层键列表 + `size` + 元素个数/行数（结果可判为数组/表时）。
- **为何**：让模型判断结果是否为空/残缺，避免「拿空结果继续用」。

## Risks / Trade-offs

- [风险] role:tool 回填对 MiniMax 严格校验敏感（assistant content null、多 system 拒）。→ 缓解：已核对现状满足；实现时保 content 非 null（用 `""`）、不 emit `strict`/`tool_choice`、system 单条。
- [风险] 成组裁剪在超长任务下可能整组删掉较早但仍有用的原生配对。→ 缓解：与既有「每 8 轮 trim」一致，优先丢最旧组，属可接受。
- [取舍] 双模式并存（原生 + 文本）增加解析分支。→ 缓解：单一响应形态一致（原生响应走原生步骤、文本响应走文本步骤），不混用。
- [取舍] 原生 `assistant` 消息在 `tool_calls` 之外的 `content` 与归一化文本骨架并存，可能冗余。→ 缓解：`content` 取模型原内容剥离泄漏令牌，不入协议骨架。

## Open Questions

- `tool_result_clip` 路由 clamp 上限具体值（建议 100000，OpenSpec 可调）。
- 原生 `assistant` 消息在 `tool_calls` 之外的 `content` 取值（建议取模型原 content 剥离泄漏令牌）。
- `chat_completion` 如何把原生消息回传给调用方（`collect_native` 参数或返回结构），属实现细节。
- `ContextManager` 新增 `push_assistant_native` / role:tool 推送方法的具体签名，属实现细节。
- `_cached_reference` 命中时是否回传落盘 `path`（让模型 READ 复用），还是仅一句「勿重复」——若仅提示不带 path，R6 去重无法真正复用数据。
