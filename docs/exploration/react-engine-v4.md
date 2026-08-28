# React Engine v4 — 需求整理（OpenSpec 需求输入）

> 本文是 grill-me 阶段产出的**需求输入**，用于进入 OpenSpec 之前对齐范围与验收口径。
> 只陈述「要解决什么问题、怎么判定做对了」，不写实现细节；实现方案由后续 OpenSpec 产出。
>
> 背景驱动：一次真实失败任务「充值用户数据导出」（16 字段、5163 uid、命中 200 轮上限、产出畸形脚本）暴露的 5 个问题补充。
>
> **状态：需求已锁定**（两轮 grilling 全 A：第一轮 §2 D9–D12，第二轮 §2 D13–D16；原 §5 未决项已收敛）。

---

## 0. 铁律（最高优先级，贯穿全部需求）

> **无任何静态硬门禁。**
>
> - 不新增阈值 / 计数器 / 确定性强制停止。
> - 循环只由四类事件结束：① 模型输出 `FINAL:`/`done`；② 用户取消；③ LLM 错误；④ `max_iters` 预算耗尽。
> - 所有「纠正」走软 LLM 判断 + coach hint，不硬停。
> - 能删的冗余代码一律删除（删除优先于新增）。
>
> R5 的 role:tool 是「结果传递形态」改变，**不引入任何新计数器 / 阈值 / 硬停**；上下文成组裁剪同样不带门禁。

---

## 1. 已确认需求（7 条：R1–R7）

### R1 — 输出内容不再被 6000 字符硬截断

**问题**：`describe` / 聚合查询结果超过 6000 字符被截断，模型看不到完整字段映射，字段映射从未被蒸馏进 PLAN，导致 200 轮空转、结果集缺字段。

**确认口径（A）**：

- 工具结果截断上限**不再是模块级硬编码常量**，改为 **Agent 可配置值** `tool_result_clip`（默认 6000）。
- LLM 单轮 `max_tokens` 由 4096 提升到 **8192**。
- MCP 结果**不再以「>6000 字符」为落盘门槛**：**所有** MCP 查询结果一律物化落盘为 `task/<run_ts>/mcp_result_N.json` 并写入去重 cache；附**结构化摘要**（JSON 顶层键列表 + 元素个数/行数，用于判断结果是否为空/残缺）。上下文的 `tool_result_clip` 截断保持不变（只影响展示，不影响落盘）。

**透出要求（Q4=A）**：`tool_result_clip` 必须可经 API / UI 写入——完全对齐 `mcp_soft_circuit` 的四处接线：
① `routers/agent.py` `AgentBody` 声明 `tool_result_clip: int = 6000` + 写逻辑 clamp（`max(1, min(.., 100000))`，风格同 `mcp_soft_circuit`）；② `startup.py` `ALTER TABLE` 加列（存量库）；③ 前端 `Agents.vue` 表单暴露。当前缺陷：只有模型列 + runtime 使用，无路由/迁移/前端接线。

### R2 — Python 脚本不再混入错误闭合代码（MiniMax 原生令牌泄漏）

**问题**：产出脚本出现畸形闭合：文件名多 `]`（`gen_batches.py]`）；内容混入 `]<]minimax[>[</old>]<]minimax[>[<new>`。

**确认口径（A）**：

- 泄漏令牌剥离正则**同时覆盖** ChatML `<|...|>` 与 MiniMax `]<...>[`。
- 路径归一化剥离首尾方括号。
- 解析工具步骤前、`extract_chat_response_text` 归一化原生 tool_calls 时，先剥离泄漏令牌。

### R3 — MCP 子任务提速

**确认口径（A）**：

- MCP 会话复用 + 工具目录 `tools/list` 跨 run 缓存（600s TTL）。
- 系统提示「取数效率」引导：批量数据优先日期区间 / 聚合谓词一次取全，不逐 uid query；落盘 `mcp_result_*.json` 用 SHELL + pandas 后处理。

### R4 — 步骤可见性：显示真实命令而非裸 `[shell]`/`[mcp_tool_call]`

**确认口径（A）**：

- 工具步骤标题改为**真实命令 + 参数预览**（`_tool_step_title`）。
- **成功步骤也保留内容**（此前仅 error 存 content），截断显示。

### R5 — LLM 使用上一轮工具结果后再进入下一轮（role:tool 原生回填）

**问题**：模型上一轮工具结果未正确进入下一轮，出现「用上一轮结果前就进入下一轮」脱节。

**确认口径（Q1/Q2/Q3 全 A）**：

- **Q1=A 双模式回填**：原生 `tool_calls` 响应 → 结果以 `role:tool` + 匹配 `tool_call_id` 回填；文本协议响应 → 继续 `role:user`。保留原生 function-calling（openai/minimax/deepseek），其结构化参数直接降低 R2 畸形脚本概率。
- **Q2=A 步骤单一来源**：原生响应直接由 `tool_calls` 生成 `ToolStep`（每个 call → 一个 step，`tool_call_id` 直接带上），**不再**二次文本解析；文本响应才走 `extract_tool_steps`。避免「归一化成文本再按顺序回填 id」的错位风险。
- **Q3=A 成组裁剪**：`fit_messages_to_context` 中 `assistant(tool_calls)` + 其后连续 `role:tool` 作为一个原子组，裁剪时整组删；不得按条删导致配对孤立。
- **归一化链路透传**：`normalize_chat_messages` / `fit_messages_to_context` 必须保留 `tool_calls` / `tool_call_id` / `role:tool` 字段。

### R6 — 查询结果去重软提示（不再重复执行相同 MCP 查询）

**问题**：模型在上一轮结果丢失后反复执行相同 MCP SQL，耗尽 200 轮。

**确认口径（Q2/Q4 全 A）**：

- 记录「已执行查询签名」= `mcp_id + tool + 规范化参数`（JSON 键保留、值参与比较，忽略空白与键序）。
- 再次出现相同签名时命中 cache，**软提示**「该查询已缓存，结果在 `task/<ts>/mcp_result_N.json`，用 READ 复用，勿重复调用」——无硬门禁。
- 仅 MCP 查询参与去重；RAG / httpmcp 暂不纳入（预留统一接口）。

### R7 — 进度回显（每轮有效，不新增机制）

**确认口径（Q3/Q6/Q9 全 A）**：

- 复用现有 `progress_block`（【本轮进度】）+ `add_progress` 相邻去重。
- 检测到重复签名命中、或模型重发 `PLAN:` 时，把「已落盘清单」（`tool → path`）回显进 coach hint。
- **不新增**轮数计数器 / 阈值 / 硬停。

> **第二轮 grilling 根因收敛**：第二轮失败案例（同一任务「零产出」）揭示，三个问题（无中间产物 / 重复语句 / 每轮无效）不是「要新建三套机制」，而是**一处门禁挡住了三套已存在机制**——`mcp_client._materialize` 只在 `len(text) > 6000` 时才落盘并填充 `query_cache`。于是普通大小的 MCP SQL 结果不落盘、不缓存、不去重，只被裁剪进上下文 → 丢失 → 重复 → 预算耗尽。**去掉该门禁（所有 MCP 结果一律落盘 + 入 cache），R1 全量落盘、R6 去重软提示、R7 进度回显同时生效。**

---

## 2. 关键架构决策

| # | 决策 | 说明 |
|---|------|------|
| D1 | 单 LLM 驱动 ReAct 循环 | 沿用 `_run_modular`，引擎只做协议解析 + 执行 + 安全拦截 + 软提示 + 少量硬停 |
| D2 | 协议标记 | `SHELL:/WRITE:/READ:/PATCH:/MCP:/RAG:/SKILL_MD:/RUN_SKILL:/RECALL:/PLAN:/FINAL:` 为解析边界唯一来源 |
| D3 | 原生 function-calling 归一化 | provider（openai/minimax/deepseek）走 `tools`+`tool_calls`；其余文本回退 |
| D4 | 截断上限 Agent 可配置 | `tool_result_clip`（Integer，默认 6000），替代模块常量 |
| D5 | `max_tokens = 8192` | 主循环 LLM 调用单轮输出上限提升 |
| D6 | 工具结果回填形态 | 原生 → `role:tool`+`tool_call_id`；文本 → `role:user`（Q1=A） |
| D7 | MCP 会话复用 + 目录缓存 | `McpSessionManager` 复用；`_get_mcp_tools_cached` 600s TTL |
| D8 | 结果物化 + 结构摘要 | MCP 结果一律落盘 `mcp_result_N.json`（无 6000 门槛），附键列表 + 元素个数摘要 |
| D9 | 双模式回填分流 | 按「响应是否含 `tool_calls`」分流回填形态，单一响应形态一致 |
| D10 | 原生步骤单一来源 | 原生 `tool_calls` 直接生成 `ToolStep`（带 id），不二次文本解析（Q2=A） |
| D11 | 成组裁剪 | `assistant(tool_calls)` + 连续 `role:tool` 原子组，整组删（Q3=A） |
| D12 | `tool_result_clip` 全透出 | 路由 + 迁移 + 前端三处补齐，对齐 `mcp_soft_circuit`（Q4=A） |
| D13 | 全量落盘（去门禁） | 所有 MCP 结果落盘 + 入去重 cache；仅 MCP，RAG/httpmcp 预留接口（第二轮 Q1/Q5/Q7） |
| D14 | 查询签名去重 + 软提示 | 签名 = `mcp_id + tool + 规范化参数`；重复时 coach hint 软提示（复用 `query_cache`，无硬门禁）（Q2/Q4） |
| D15 | 进度回显 | 复用 `progress_block`；重复命中 / 重发 PLAN 时回显已落盘清单，不新增机制（Q3/Q6/Q9） |
| D16 | 摘要含元素个数 | 顶层键 + size + 元素个数/行数（Q8） |

---

## 3. 边界条件

| 边界 | 约束 |
|------|------|
| 铁律 | 无任何静态硬门禁；`soft_circuit` 等数值只作提示文案参考，不触发硬停 |
| 唯一预算约束 | `max_iters`（Agent `max_iterations`，默认 50，本次失败案例 200） |
| 截断下限 | `tool_result_clip` 取值 `max(1, …)`；路由 clamp 上限 100000（输入校验，非门禁） |
| 历史层卫生 | `_history_reply` 单条 2000 字符上限，WRITE 正文压缩为首行 |
| 步骤内容预览 | 工具步骤 content 预览截断（300 字符） |
| MCP 目录缓存 | `tools/list` 600s TTL（跨 run） |
| MCP 结果落盘 | 所有 MCP 结果一律落盘（文件数 = 查询数）；上下文 `tool_result_clip` 截断不变 |
| 去重签名 | `mcp_id + tool + json.dumps(args, sort_keys=True)`；cache LRU cap 100 |
| 软失败熔断 | `mcp_soft_circuit` 默认 5，仅软提示 |
| 泄漏令牌正则 | 同时匹配 `<\|...\|>` 与 `]<...>[` |
| MiniMax 严格校验 | assistant 带 `tool_calls` 时 `content` 非 `null`（用 `""`）；不 emit `strict`/`tool_choice`；单条 `system`（已满足） |
| 成组裁剪原子性 | 原生配对组不可拆散删除 |

---

## 4. Acceptance Criteria

1. **R1**：`tool_result_clip` 可经 API 写入（`AgentBody` 声明 + 写逻辑 clamp）；存量库经 `startup.py` 得到该列；前端可编辑；未配置默认 6000 行为不变；上下文截断按 `tool_result_clip` 生效；**所有 MCP 结果一律落盘 + 入去重 cache**（不因 ≤6000 而跳过）；`max_tokens = 8192`。
2. **R2**：含 `]<...>[` / `<|...|>` 令牌的模型回复，解析后的工具步骤与落盘脚本中不再出现泄漏令牌；`xxx.py]` 被归一化为 `xxx.py`。
3. **R3**：同一运行内同一 MCP 多次调用复用会话；目录 `tools/list` 命中缓存不重复请求。
4. **R4**：`_tool_step_title` 输出真实命令 + 参数预览；成功步骤与失败步骤都有可见 content。
5. **R5**：原生 `tool_calls` 的结果以 `role:tool` + 匹配 `tool_call_id` 回填；经 `normalize_chat_messages` / `fit_messages_to_context` 后 `tool_calls`/`tool_call_id`/`role:tool` 不丢失；上下文裁剪不产生孤立 tool 消息（成组删）。
6. **回归**：全量测试通过；短任务 / 纯文本协议 / 非原生 provider 路径行为不变；`build_tool_schemas` 不 emit `strict`/`tool_choice`。
7. **R6**：同一 MCP 查询（`mcp_id + tool + 规范化参数` 一致）再次出现时，命中 cache、返回「已缓存」软提示（含落盘 path），不重复请求；软提示、无硬门禁。
8. **R7**：检测到重复命中或模型重发 `PLAN:` 时，进度回显「已落盘清单」（`tool → path`）；不新增轮数计数器 / 阈值。

---

## 5. 遗留项（已收敛，交给 OpenSpec 定实现，非需求分歧）

1. `tool_result_clip` 路由 clamp 上限具体值（建议 100000，OpenSpec 可调）。
2. 原生 `assistant` 消息在 `tool_calls` 之外的 `content` 取什么（建议取模型原 content 剥离泄漏令牌；OpenSpec 定）。
3. `chat_completion` 如何把原生消息回传给调用方（如 `collect_native` 参数或返回结构），属实现细节。
4. `ContextManager` 新增 `push_assistant_native` / role:tool 推送方法的具体签名，属实现细节。
5. `_cached_reference` 命中时是否回传落盘 `path`（让模型 READ 复用），还是仅一句「勿重复」——需实现期确认；若仅提示不带 path，R6 去重无法真正复用数据。

---

## 6. 相关文档

- 引擎内部地图（现状）：[`../react-engine.md`](../react-engine.md)
- 本次 grill-me 原始问题补充（充值用户数据导出失败复盘）：见会话记录
