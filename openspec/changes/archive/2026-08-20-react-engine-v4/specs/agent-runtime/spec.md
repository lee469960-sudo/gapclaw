## ADDED Requirements

### Requirement: 工具结果截断上限由 Agent 配置、MCP 结果全量落盘且输出上限提升

工具结果截断上限 MUST 是可经 Agent 配置的值（默认 6000），而非模块级硬编码常量；该配置值 MUST 作用于工具结果入上下文的裁剪。所有非空 MCP 查询结果 MUST 一律物化落盘（不因 ≤ 截断阈值而跳过），并写入去重 cache；落盘摘要 MUST 含 JSON 顶层键列表与元素个数/行数。主循环 LLM 调用 MUST 以 8192 作为单轮输出上限。该配置 MUST 可经 API 与 UI 写入（请求体声明 + 写逻辑夹取 + 存量库迁移加列 + 前端表单）；未配置时默认 6000 行为不变。

#### Scenario: 配置截断上限生效

- **WHEN** Agent 配置了 `tool_result_clip = 12000`
- **THEN** 工具结果入上下文按 12000 字符裁剪（而非 6000）

#### Scenario: 未配置时默认 6000

- **WHEN** Agent 未配置 `tool_result_clip`
- **THEN** 上下文截断按 6000 处理，行为与现状一致

#### Scenario: MCP 结果全量落盘

- **WHEN** 执行任意非空 MCP 查询
- **THEN** 结果一律物化落盘为 `task/<run_ts>/mcp_result_N.json` 并写入去重 cache，即使结果 ≤ 截断阈值也不跳过

#### Scenario: 落盘摘要含元素个数

- **WHEN** MCP 结果落盘
- **THEN** 摘要含 JSON 顶层键列表 + 元素个数/行数

#### Scenario: 配置可经 API 与 UI 写入

- **WHEN** 通过 API 创建 / 更新 Agent 传入 `tool_result_clip`
- **THEN** 该值被持久化并生效
- **AND** 存量数据库经迁移得到该列，前端表单可编辑该值

#### Scenario: 主循环输出上限 8192

- **WHEN** 主循环调用 LLM
- **THEN** 单轮输出上限为 8192

### Requirement: 泄漏的原生工具令牌与畸形闭合不得进入可执行内容

解析模型回复时，引擎 MUST 剥离泄漏的原生 tool-call 特殊令牌（同时覆盖 ChatML `<|...|>` 与 MiniMax `]<...>[` 形式）；路径归一化 MUST 剥离首尾方括号，使 `xxx.py]` 被归一化为 `xxx.py`。该剥离 MUST 在解析工具步骤与归一化原生 tool_calls 之前发生，保证令牌不进入可执行内容或落盘脚本。

#### Scenario: 剥离 MiniMax 分隔符

- **WHEN** 模型回复内容混入 `]<...>[` 形式的原生分隔符
- **THEN** 解析出的工具步骤与落盘内容中不再出现该令牌

#### Scenario: 归一化畸形文件名

- **WHEN** 模型输出的路径带首尾方括号（如 `gen_batches.py]`）
- **THEN** 路径被归一化为 `gen_batches.py`

### Requirement: 绑定 MCP 时复用工具目录缓存并提供取数效率引导

工具目录 `tools/list` MUST 跨运行缓存（TTL 600 秒），命中缓存时不重复请求。绑定 MCP 时，系统提示 MUST 包含「取数效率」引导：批量数据优先用日期区间 / 聚合谓词一次取全，不逐 uid 逐条 query；物化落盘的结果用 SHELL + pandas 后处理，避免反复 MCP 往返。该引导 MUST 保持中性、不点名具体工具。

#### Scenario: 工具目录命中缓存

- **WHEN** 在 TTL 内再次解析同一 MCP 的工具目录
- **THEN** 命中缓存，不重复发起 tools/list 请求

#### Scenario: 绑定 MCP 注入取数效率引导

- **WHEN** agent 绑定至少一个 MCP
- **THEN** 系统提示包含取数效率引导
- **AND** 引导不点名具体工具

### Requirement: 工具执行步骤展示真实命令与成功内容

工具执行步骤的标题 MUST 展示真实命令或工具名加参数预览（而非裸 `[shell]` / `[mcp_tool_call]`）。成功步骤与失败步骤 MUST 都保留内容（截断显示），使 UI 能看到每步做了什么。

#### Scenario: 标题显示真实命令

- **WHEN** 执行一个 shell 工具步骤
- **THEN** 步骤标题含真实命令与参数预览

#### Scenario: 成功步骤保留内容

- **WHEN** 工具执行成功
- **THEN** 步骤保留结果内容（截断显示），而非仅错误时保留

### Requirement: 原生 tool_calls 的结果以 role:tool 回填并全程保持配对

当模型以原生 `tool_calls` 返回工具调用时，每个调用的结果 MUST 以 `role:tool` + 匹配的 `tool_call_id` 回填，而非 `role:user`；文本协议回复仍以 `role:user` 回填。原生 `tool_calls` MUST 直接生成带 `tool_call_id` 的工具步骤（单一来源，不二次文本解析）。归一化链路 MUST 透传 assistant 的 `tool_calls` 与 tool 消息的 `tool_call_id`；上下文裁剪时 MUST 将 `assistant(tool_calls)` 及其后连续的 `role:tool` 消息作为一个原子组整组删除，不得拆散。

#### Scenario: 原生调用结果 role:tool 回填

- **WHEN** 模型返回原生 `tool_calls` 且工具执行完成
- **THEN** 结果以 `role:tool` + 匹配 `tool_call_id` 回填到上下文

#### Scenario: 文本协议回复仍 role:user 回填

- **WHEN** 模型以文本协议（无 `tool_calls`）返回工具调用
- **THEN** 结果以 `role:user` 回填，行为不变

#### Scenario: 归一化透传配对字段

- **WHEN** 含原生配对的上下文经 `normalize_chat_messages` / `fit_messages_to_context`
- **THEN** assistant 的 `tool_calls` 与 tool 消息的 `tool_call_id` 字段不丢失

#### Scenario: 成组裁剪不孤立 tool 消息

- **WHEN** 上下文超预算需要裁剪
- **THEN** `assistant(tool_calls)` 与其后连续的 `role:tool` 消息整组删除，不出现无对应 tool_call 的孤立 tool 消息

### Requirement: 相同 MCP 查询以软提示去重而不重复执行

引擎 MUST 记录「已执行查询签名」（MCP id + 工具名 + 规范化参数，忽略空白与键序）；当相同签名再次出现时 MUST 命中 cache、以软提示告知「该查询已缓存、结果路径、用 READ 复用、勿重复调用」，而非重复请求 MCP 服务。该去重 MUST 为软提示，不引入任何硬门禁或强制停止；仅 MCP 查询参与去重。

#### Scenario: 相同查询命中缓存

- **WHEN** 模型再次发出与已执行查询相同的 MCP 调用（`mcp_id + tool + 规范化参数` 一致）
- **THEN** 引擎不重复请求 MCP 服务
- **AND** 返回软提示（含落盘 path），模型可用 READ 复用结果

#### Scenario: 参数不同的查询不算重复

- **WHEN** 模型发出的 MCP 调用参数值与已执行查询不同
- **THEN** 视为新查询，正常执行，不触发去重提示

### Requirement: 进度回显已落盘清单

检测到重复签名命中、或模型重发 `PLAN:` 时，引擎 MUST 把「已落盘清单」（工具 → 路径）回显进进度 / coach hint，使模型知道已产出哪些中间产物。该回显 MUST 复用现有进度块与 coach hint 聚合，不新增轮数计数器 / 阈值 / 硬停。

#### Scenario: 重复命中时回显清单

- **WHEN** 检测到重复签名命中
- **THEN** 进度 / coach hint 回显已落盘清单（tool → path）

#### Scenario: 重发 PLAN 时回显清单

- **WHEN** 模型重发 `PLAN:`
- **THEN** 进度回显已落盘清单，不新增计数器或强制停止
