# agent-runtime Specification

## Purpose

单 LLM 驱动的 ReAct 运行时,负责协议解析、工具执行、安全拦截、断点续跑与软性教练提示。本规格定义其 v1 行为契约:协议回复中 FINAL 与工具共现的处置、MCP 连接复用、checkpoint 保真、查询去重、续跑上下文注入、最终复核、多 MCP 分派,以及多软提示的合并呈现。

## Requirements

### Requirement: FINAL 与工具同轮共现时显式告警被跳过的工具

当单轮 LLM 回复同时包含一个 FINAL 步骤与一个或多个非 PLAN 工具步骤时,引擎 MUST 保持 FINAL 优先(被跳过的工具不执行),并 MUST 产生一条可被用户观察到的执行步骤告警与一条面向模型的软性教练提示,明确指出本轮哪些工具未执行。PLAN 步骤不属于被丢弃的工具,不触发该告警。

#### Scenario: FINAL 与 SHELL 同轮

- **WHEN** 单轮回复同时包含 `SHELL: <cmd>` 与 `FINAL: <payload>`
- **THEN** 引擎不执行 SHELL,并按 FINAL 收尾(或进入完成度复核)
- **AND** 产生一条可见步骤告警,其内容指明「本轮 FINAL 与工具同时出现,工具未执行」

#### Scenario: 仅 FINAL 无工具步骤

- **WHEN** 单轮回复仅包含 `FINAL: <payload>` 且不含任何工具步骤
- **THEN** 引擎正常收尾
- **AND** 不产生「工具被跳过」的额外告警

#### Scenario: FINAL 与 PLAN 同轮

- **WHEN** 单轮回复同时包含 `PLAN:` 与 `FINAL: <payload>`
- **THEN** PLAN 先被应用(更新任务上下文与子任务清单),随后按 FINAL 收尾
- **AND** 不触发「非 PLAN 工具被跳过」的告警

### Requirement: MCP 连接在单次运行内复用

运行时 MUST 在单次运行内复用同一 MCP 的连接:对同一 MCP 的多次工具调用只建立一次连接(streamable 只 `initialize` 一次、stdio 只 spawn 一个子进程),后续调用复用该会话。运行结束(正常收尾、取消、停止或异常)时 MUST 关闭全部会话并终止 stdio 子进程。复用会话发生失效(stdio 子进程退出、streamable session 过期)时 MUST 重建会话并重试一次。

#### Scenario: 同一 MCP 多次调用复用会话

- **WHEN** 单次运行内对同一 MCP 连续执行多次工具调用
- **THEN** 仅第一次调用建立连接,后续调用复用同一会话,不重复握手、不重复 spawn 子进程

#### Scenario: 运行结束关闭会话

- **WHEN** 运行结束(含取消、停止、异常等提前终止路径)
- **THEN** 该运行建立的全部 MCP 会话被关闭,stdio 子进程被终止,不遗留进程

#### Scenario: 会话失效后恢复

- **WHEN** 复用会话时连接已失效(stdio 子进程已退出,或 streamable session-id 已过期)
- **THEN** 运行时重建会话并重试该调用一次

#### Scenario: 复用与失效可观测

- **WHEN** 会话被复用、重建或关闭
- **THEN** 产生可观测的执行步骤或日志记录,使调试不因复用而退化

### Requirement: 断点续跑 checkpoint 保真

长任务的断点 checkpoint MUST 持久化 `run_ts`(首轮产物目录时间戳)、`mcp_results`(已落盘 MCP 结果清单)、`query_cache`(查询去重缓存)、`plan_text`(原始 PLAN 正文),并在续跑时原样恢复;续跑 MUST 复用同一 `run_ts` 对应的产物目录,不再新建孤立产物目录。

#### Scenario: 续跑复用同一产物目录

- **WHEN** 长任务首轮已在 `task/<ts>/` 落盘产物并写入 checkpoint,随后续跑
- **THEN** 续跑复用同一 `<ts>` 目录,已有产物不被孤立、不新建目录

#### Scenario: 扩展字段 roundtrip 保真

- **WHEN** checkpoint 保存后重新加载
- **THEN** `run_ts`、`mcp_results`、`query_cache`、`plan_text` 原样恢复(旧 checkpoint 缺这些字段时按空处理)

### Requirement: 相同 MCP 查询在单次运行内去重

运行时 MUST 在单次运行内按 `(mcp_id, tool_name, 规范化参数)` 对 MCP 查询去重（规范化参数 MUST 忽略空白与键序）:命中已缓存结果时 MUST 不重调 MCP、不新增结果文件、不重复写入工具结果,仅以软提示返回指向已落盘结果的引用（告知「该查询已缓存、结果路径、用 READ 复用、勿重复调用」）;未命中时 MUST 照常调用并按需落盘记入缓存。该去重 MUST 为软提示,不引入任何硬门禁或强制停止;仅 MCP 查询参与去重。

#### Scenario: 重复查询命中缓存

- **WHEN** 单次运行内第二次以相同参数查询同一 MCP 工具
- **THEN** 不重调 MCP、不新增 `mcp_result_N.json`,返回「已缓存,结果见 <path>」的引用

#### Scenario: 不同参数不命中缓存

- **WHEN** 查询参数与已缓存条目不同
- **THEN** 照常调用 MCP,并按需落盘新结果、记入缓存

### Requirement: 续跑时任务上下文包含子任务清单与计划正文

续跑 MUST 将渲染后的子任务清单(含 `[x]`/`[ ]` 状态)与 `plan_text` 注入任务上下文,并把已恢复的 `mcp_results` 与 `saved_paths` 回填进度块,使模型能识别已完成与待办的子任务、跳过已确认的产物。

#### Scenario: 续跑上下文含子任务与计划

- **WHEN** 从 checkpoint 续跑一个已有多条子任务的长任务
- **THEN** 模型上下文的任务目标层包含渲染后的子任务清单与计划正文,进度块包含已恢复的产物路径

### Requirement: 子任务推进时提示模型重发完整 PLAN

当子任务清单发生变化(新 PLAN 被应用或进度推进)时,运行时 MUST 注入一条软性提示,提醒模型在完成子任务后重发带 `[x]` 的完整 PLAN。该提示不进行任何进度计数或硬性打断。

#### Scenario: 子任务推进后提示

- **WHEN** 运行时应用了新 PLAN 或子任务进度发生变化
- **THEN** 注入一条教练提示,提醒「完成子任务后重发带 [x] 的完整 PLAN」,且不产生硬性计数或循环中断

### Requirement: 最终回复复核依据子任务完成度

对候选 FINAL 做完成度复核时,复核输入 MUST 包含渲染后的子任务清单与最近进度,使复核能按子任务完成度判定,而非仅依据目标与已保存文件。

#### Scenario: 复核输入含子任务清单与进度

- **WHEN** 引擎对候选 FINAL 执行完成度复核
- **THEN** 复核提示包含渲染后的子任务清单与最近进度

### Requirement: 多 MCP 绑定通过 LLM 语义路由惰性发现并正确分派工具调用

当 agent 绑定多个 MCP 时，运行时 MUST 在目录发现前使用 Agent 配置的 LLM，根据用户请求、当前运行上下文和合格候选 MCP 的名称、标签及描述，选择零个、一个或多个 MCP。路由输入 MUST NOT 包含候选 MCP 的完整工具目录。运行时 MUST 仅对选中的 MCP 连接并调用 `tools/list`，再将其目录提供给主 Agent；未选 MCP MUST 不连接、不调用 `tools/list`、不创建目录缓存。用户点名 MCP 时，运行时 MUST 将点名作为路由 LLM 的强信号，而非绕过路由。路由输出 MUST 在服务端验证为当前 Agent 已绑定且调用权限有效的 MCP。对于已发现目录中的工具调用，运行时 MUST 分派到实际声明该工具的已选 MCP，而非固定返回第一个绑定 MCP。

#### Scenario: 单域请求只加载语义匹配的 MCP

- **WHEN** Agent 同时绑定能力描述为 ClickHouse 性能优化与数据导出/查询的 MCP，且用户请求性能优化
- **THEN** 路由 LLM 可以仅选择前者
- **AND** 系统仅对前者执行连接和 `tools/list`
- **AND** 后者不出现在主 Agent 的工具目录中

#### Scenario: 跨域请求选择多个 MCP

- **WHEN** 用户请求同时需要性能分析和数据导出/查询
- **THEN** 路由 LLM 可以选择多个合格 MCP
- **AND** 系统仅发现这些被选择 MCP 的目录

#### Scenario: 用户点名 MCP 仍经路由与授权校验

- **WHEN** 用户在请求中点名一个当前 Agent 已绑定的 MCP
- **THEN** 系统将点名作为路由输入的强信号
- **AND** 仅在路由结果通过绑定和调用权限校验后加载该 MCP

#### Scenario: 匹配工具所在已选 MCP

- **WHEN** 路由选择多个 MCP，且目标工具仅存在于其中某一个已发现目录中
- **THEN** 该工具在它实际所在的 MCP 上执行
- **AND** 单 MCP 绑定的行为不变

### Requirement: MCP 候选资格基于已绑定且可调用的 MCP

自动 MCP 路由 MUST 将当前 Agent 已绑定且调用权限有效的 MCP 全部作为候选。缺少能力描述或标签 MUST NOT 把该 MCP 排除出候选；系统 MAY 向配置者提示元数据不完整，但 MUST 仍允许其参与路由与空选择回退。

#### Scenario: 描述完整的已绑定 MCP 成为候选

- **WHEN** MCP 已绑定至 Agent、调用权限有效且具有非空 `description` 或 `tags`
- **THEN** 系统将其名称、标签和描述作为路由候选元数据

#### Scenario: 元数据缺失的已绑定 MCP 仍参加自动路由

- **WHEN** 已绑定 MCP 的 `description` 与 `tags` 均为空，且调用权限有效
- **THEN** 系统仍将其作为路由候选
- **AND** 配置者可观察到元数据不完整，但该 MCP 仍可被选中或纳入空选择回退

### Requirement: 路由失败回退到全部合格候选并受限补选

当初始路由或补选路由失败、超时、返回无效结果、无足够置信度或选出空集，且当前运行尚未加载任何 MCP 时，运行时 MUST 选中全部合格候选，MUST NOT 把已有合格候选清成零个 MCP。若运行中已有选中 MCP，空的补选结果 MUST NOT 再追加其余候选。主 Agent 仅在明确需要额外能力、已选 MCP 不可达或其目录缺少所需工具时请求补选；单次运行最多执行两次补选。补选 MUST 不重复加载已经选中的 MCP。

#### Scenario: 路由失败回退全部合格候选

- **WHEN** 路由 LLM 超时、低置信度或返回无法验证的 MCP 标识，且合格候选非空、当前未加载任何 MCP
- **THEN** 系统选中全部合格候选并对其执行目录发现
- **AND** 系统不把选择结果保持为零

#### Scenario: 缺少能力时受限补选

- **WHEN** 主 Agent 明确需要额外能力，或已选 MCP 不可达、未声明所需工具
- **THEN** 系统可以执行一次补选路由并仅加载新选中的合格 MCP
- **AND** 单次运行中的补选次数不超过两次

### Requirement: MCP 路由决策可审计且目录缓存不扩大访问范围

运行时 MUST 为每次路由和补选记录脱敏的结构化审计事件，至少含请求摘要、候选标识、选择结果、选择理由、触发原因、补选序号和加载结果。目录缓存 MUST 仅保存曾被实际选择的 MCP，并在 MCP 变更或到期后失效；缓存 MUST NOT 触发未选 MCP 的连接或目录发现。

#### Scenario: 初始选择产生脱敏审计事件

- **WHEN** 初始 MCP 路由完成
- **THEN** 系统记录不含完整提示词或凭据的结构化审计事件
- **AND** 事件含选择结果、理由和实际加载结果

#### Scenario: 未选 MCP 不因缓存而被访问

- **WHEN** 运行使用跨运行目录缓存
- **THEN** 仅先前实际选中过的 MCP 可以使用缓存
- **AND** 未被本次路由选择的 MCP 不连接、不执行目录发现

### Requirement: 多软提示在同一轮内合并而不互相覆盖

当同一迭代内产生多条软性教练提示(如 FINAL+工具告警、卡死提示、工具失败/重复提示、子任务提醒)时,运行时 MUST 将它们合并呈现,任一提示 MUST NOT 被后写的提示静默覆盖。

#### Scenario: 多提示同轮合并

- **WHEN** 同一迭代内同时产生多条不同来源的教练提示
- **THEN** 这些提示合并为一条完整呈现,无一条被覆盖丢失

#### Scenario: 单提示照常呈现

- **WHEN** 某迭代仅产生一条教练提示
- **THEN** 该提示正常呈现,行为与合并前一致

### Requirement: PLAN-only 轮次注入「规划待执行」软提示

当单轮回复只包含一个 PLAN 步骤、不包含任何工具步骤（且不含 FINAL）时，引擎 MUST 注入一条软性教练提示，提醒模型「本轮已记录计划但未调用任何工具，请对第一个待办子任务输出实际的工具调用行」。该提示 MUST NOT 进行进度计数或触发硬门禁；当本轮存在被执行的工具或 FINAL 时，MUST NOT 发该提示。

#### Scenario: PLAN-only 一轮后注入提示

- **WHEN** 单轮回复只包含 `PLAN: <计划>` 且不含任何工具步骤、FINAL 或裸代码
- **THEN** 引擎注入一条软提示，内容指引模型对第一个 `[ ]` 子任务输出实际的工具调用行
- **AND** 不产生任何硬性计数或循环中断

#### Scenario: PLAN 与工具同轮不发提示

- **WHEN** 单轮回复同时包含 `PLAN:` 与至少一个工具步骤（如 `MCP:`/`SHELL:`）
- **THEN** 引擎正常执行工具，不注入「规划待执行」提示

#### Scenario: PLAN 与 FINAL 同轮不发提示

- **WHEN** 单轮回复同时包含 `PLAN:` 与 `FINAL: <payload>` 且无其它工具步骤
- **THEN** 引擎按 FINAL 收尾，不注入「规划待执行」提示

### Requirement: 同一 MCP 工具大量调用时注入映射蒸馏软提示

单次运行内 MUST 按 `mcp:<tool_name>` 聚合同一 MCP 工具的成功调用次数（`<tool_name>` 从 `MCP:` 行提取的真实工具名，而非笼统的协议名），并与其调用参数是否相同无关；达到 4 次起、每 +2 次注入一条「映射蒸馏」软提示，指引模型把已确认的 `view→字段/口径` 映射蒸馏进 PLAN 或落盘，而非逐个 describe 大量资源。该提示 MUST NOT 进行硬门禁或中断循环；模型输出新 PLAN 时 MUST 重置该计数。完成度复核的修订 PLAN MUST NOT 重置该计数（因为引擎不再应用该修订 PLAN）。

#### Scenario: 达到阈值注入蒸馏提示

- **WHEN** 同一 MCP 工具在单次运行内被成功调用累计到第 4 次，并在此后每 +2 次（第 6、8…次）
- **THEN** 引擎注入一条「映射蒸馏」软提示，指引把 view→字段/口径映射蒸馏进 PLAN 或落盘
- **AND** 不产生任何硬性计数中断或循环终止

#### Scenario: 不同参数聚合计数

- **WHEN** 同一 MCP 工具以不同参数被多次调用（如逐个 describe 不同 view）
- **THEN** 这些调用按工具名聚合计数，而非按精确参数各自独立

#### Scenario: 新 PLAN 重置计数

- **WHEN** 模型输出新的 PLAN 且当前不在修复期（PLAN 被应用）
- **THEN** 该工具调用计数重置为零，后续调用重新累计

### Requirement: 超大 MCP 结果物化时内联结构摘要

当 MCP 结果因过大被物化到 `mcp_result_N.json` 时，若结果为 JSON，引擎 MUST 在「已写入」通知里内联一段结构摘要（顶层 key + 数组元素字段名，≤320 字符），使模型无需读取完整文件即可取得字段名以建立映射。非 JSON 或空 payload 时 MUST NOT 追加摘要行。

#### Scenario: JSON 对象结果提取顶层 key

- **WHEN** 物化的结果为 JSON 对象
- **THEN** 通知内联一段结构摘要，含顶层 key 名（及前若干数组元素的字段名）

#### Scenario: JSON 数组结果提取首元素字段名

- **WHEN** 物化的结果为非空 JSON 数组
- **THEN** 通知内联一段结构摘要，含数组项数与首元素字段名

#### Scenario: 非 JSON 或空 payload 不加摘要行

- **WHEN** 物化结果非 JSON，或为空数组 / 空对象
- **THEN** 不产生结构摘要行，通知保持原有形态

### Requirement: 绑定 MCP 时系统提示含资源映射指引

当 agent 绑定至少一个 MCP 时，系统提示 MUST 包含「资源映射」指引：原始 list/describe/query 结果可能被截断或落盘（过大时写入 `mcp_result_*.json`），确认的 `view→字段/口径` 映射必须蒸馏进 PLAN 或落盘文件，不要逐个 describe 大量资源——先按字段名/口径定位候选，再 describe 确认。该指引文案 MUST 保持中性、不点名任何具体工具。

#### Scenario: 绑定 MCP 时注入资源映射指引

- **WHEN** agent 绑定至少一个 MCP
- **THEN** 系统提示包含资源映射指引
- **AND** 指引不硬编码任何具体工具名

### Requirement: 预算临近提示须包含落盘保留语义

预算临近软提示 MUST 提醒模型优先落盘中间产物再收尾，并明确已落盘内容在断点续跑时保留。触发时机不变（剩余轮次为 5 或 2），且 MUST NOT 引入硬门禁。

#### Scenario: 预算临近时提示落盘保留

- **WHEN** 剩余轮次为 5 或 2
- **THEN** 注入预算告急提示，内容含「落盘中间产物」与「已落盘内容断点续跑保留」语义
- **AND** 不产生硬性中断

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

当模型以原生 `tool_calls` 返回工具调用时，每个调用的结果 MUST 以 `role:tool` + 匹配的 `tool_call_id` 回填，而非 `role:user`；文本协议回复仍以 `role:user` 回填。原生 `tool_calls` MUST 直接生成带 `tool_call_id` 的工具步骤（单一来源，不二次文本解析）。归一化链路 MUST 透传 assistant 的 `tool_calls` 与 tool 消息的 `tool_call_id`；上下文裁剪时 MUST 将 `assistant(tool_calls)` 及其后连续的 `role:tool` 消息作为一个原子组整组删除，不得拆散。当某条原生 `tool_calls` 因权限未启用被阻止、或因 FINAL 同轮被跳过而未执行时，引擎 MUST 仍为其回填一个合成 `role:tool` 结果（明确「未执行」原因），保证每条 assistant `tool_calls` 都有配对的 tool 消息，避免下一轮协议校验失败。

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

#### Scenario: 被阻止或跳过的原生调用也回填合成结果

- **WHEN** 原生 `tool_calls` 中的某条因权限未启用被阻止、或因 FINAL 同轮被跳过而未执行
- **THEN** 引擎仍为其回填一个合成 `role:tool` 结果（明确「未执行」原因）
- **AND** 下一轮发送给模型的消息中，每条 assistant `tool_calls` 均有配对的 tool 消息，不触发协议校验失败

### Requirement: 进度回显已落盘清单

检测到重复签名命中、或模型重发 `PLAN:` 时，引擎 MUST 把「已落盘清单」（工具 → 路径）回显进进度 / coach hint，使模型知道已产出哪些中间产物。该回显 MUST 复用现有进度块与 coach hint 聚合，不新增轮数计数器 / 阈值 / 硬停。

#### Scenario: 重复命中时回显清单

- **WHEN** 检测到重复签名命中
- **THEN** 进度 / coach hint 回显已落盘清单（tool → path）

#### Scenario: 重发 PLAN 时回显清单

- **WHEN** 模型重发 `PLAN:`
- **THEN** 进度回显已落盘清单，不新增计数器或强制停止

### Requirement: 非 FINAL 退出前持久化最新运行状态

当运行以非 FINAL 方式退出时（预算耗尽达到 `max_iters` 且存在未完成子任务、或 LLM 连续调用失败），引擎 MUST 在返回前持久化最新运行状态——包括已落盘 MCP 结果清单、查询去重缓存、进度行与已保存路径——而非仅持久化最后一次 PLAN 时的旧状态。续跑 MUST 复用同一 `run_ts` 产物目录并恢复这些状态，使模型能识别已产出中间产物、命中已缓存查询、延续进度，不重复查询。

#### Scenario: 预算耗尽时持久化最新状态

- **WHEN** 长任务因达到 `max_iters` 退出，且存在未完成的子任务
- **THEN** 返回前持久化当前 `mcp_results`、`query_cache`、`progress_lines`、`saved_paths`
- **AND** 续跑能恢复这些状态（而非仅回到最后一次 PLAN 时的旧状态）

#### Scenario: LLM 连续失败时持久化最新状态

- **WHEN** LLM 连续调用失败导致运行退出
- **THEN** 返回前持久化当前运行状态，且退出文案与真实持久化一致

#### Scenario: 续跑恢复非 FINAL 退出后的落盘状态

- **WHEN** 从一次非 FINAL 退出后续跑
- **THEN** 已落盘的 MCP 结果回填进度块（「已缓存 MCP 结果 …」），查询去重缓存生效，模型不再重复查询已缓存数据

### Requirement: 动态软提示与进度块不被截断丢弃

当 system 层合并后超出上限需要裁剪时，引擎 MUST 优先裁剪静态目录层（工具目录、技能快照等），而教练提示（`coach_hint`）与进度块（`progress_block`）MUST 在裁剪后仍完整保留，保证卡死检测、预算告急、完成度反思等软提示与「勿丢失」产物路径在长任务 / 大目录场景下不被丢弃。

#### Scenario: 大目录下软提示仍保留

- **WHEN** 工具目录与技能快照使 system 块超出上限
- **THEN** 裁剪静态目录层，教练提示完整保留

#### Scenario: 进度块路径不被丢弃

- **WHEN** 进度块记录了已落盘产物路径，且 system 块超限
- **THEN** 进度块完整保留，产物路径不因裁剪丢失

### Requirement: 完成度复核连续拒绝后收敛

对候选 FINAL 的完成度复核返回有效结构化证据缺口时，引擎 MUST 按缺口跟踪取证动作，并且每个缺口最多允许两次具有不同规范化签名的取证。所有剩余缺口均无效、已满足、无新证据价值或已耗尽时，引擎 MUST 收敛而非空转到预算耗尽：只读/分析任务接受带条件候选，高风险任务进入用户确认收尾。

#### Scenario: 连续有效拒绝后接受候选

- **WHEN** 完成度复核对候选 FINAL 连续有效拒绝达到阈值
- **THEN** 引擎接受当前候选并收尾，同时记录 / 呈现被拒原因供用户观察

#### Scenario: 拒绝循环不空转到预算耗尽

- **WHEN** 完成度复核持续返回有效 FAIL
- **THEN** 引擎在阈值内收敛，不空转至 `max_iters` 耗尽后才丢弃最终答案

#### Scenario: 工具成功清零连续计数

- **WHEN** 已有有效 FAIL 计数，随后一次非缓存命中的工具执行成功
- **THEN** 连续有效失败计数重置为 0
- **AND** 之后需重新累计满阈值才收敛

### Requirement: 复核拒绝时回灌修复清单

完成度复核返回有效失败时，引擎 MUST 把有效 `fix_list` 随教练提示回灌给模型，使模型拿到可执行缺失项。引擎 MUST NOT 将复核产出的修订 PLAN 应用到子任务状态；修复期内也 MUST NOT 把后续模型 PLAN 写进状态。

#### Scenario: 复核失败回灌修复清单

- **WHEN** 完成度复核返回有效 FAIL 且产出具体修复清单
- **THEN** 教练提示包含这些具体修复项，而非仅笼统的缺失提示
- **AND** 不把修订 PLAN 写入子任务清单

### Requirement: 子任务证据门

LLM 自主生成的 PLAN 子任务 MUST 作为进度提示和复核上下文，但 MUST NOT 单独阻止候选 FINAL。运行时 MUST 仅依据原始用户目标中可验证但未满足的需求，以及经校验仍具新证据价值的复核缺口决定是否继续；未完成 PLAN 子任务不得作为独立结束门。

#### Scenario: 未完成子任务不送复核

- **WHEN** 模型输出 `FINAL:`（或完成信号候选）且存在 `status != done` 的具名子任务
- **THEN** 引擎不调用完成度复核
- **AND** 不把该 FINAL 作为任务结束
- **AND** 教练提示包含未完成子任务文本

#### Scenario: 无子任务仍复核

- **WHEN** 子任务清单为空（或没有具名条目）且模型输出 FINAL
- **THEN** 引擎仍调用完成度复核

#### Scenario: 证据门丢掉 FINAL 后同轮工具仍执行

- **WHEN** 本轮同时包含未完成子任务下的 FINAL 与其它工具步骤
- **THEN** FINAL 被丢弃且不送复核
- **AND** 其余工具步骤按既有安全门继续执行

### Requirement: 空话复核失败视为通过

完成度复核返回 FAIL 但有效修复清单为空时，引擎 MUST 将该结果视为通过并接受候选 FINAL。有效修复清单定义为去掉空白与套话条目后仍非空。套话条目为整句匹配：`任务尚未完成`、`尚未完成`、`还未完成`、`没有完成`、`未完成`、`再检查一下`、`请再检查`、`再核对一下`、`请再核对`、`任务未完成`、`还需努力`。

#### Scenario: 仅套话 FAIL 接受候选

- **WHEN** 复核返回 FAIL 且 `fix_list` 为空或条目全部为套话
- **THEN** 引擎接受当前候选并结束运行
- **AND** 不增加有效失败计数
- **AND** 不应用修订 PLAN

#### Scenario: 含可执行项则保持 FAIL

- **WHEN** 复核返回 FAIL 且 `fix_list` 含至少一条非套话条目
- **THEN** 按有效失败处理（回灌修复清单、进入修复期或累计收敛）

### Requirement: 有效失败后修复期禁止规划

完成度复核返回有效 FAIL 之后、直到下一次候选 FINAL 被接受（通过、空话通过或收敛）之前，引擎 MUST 丢弃模型输出的任何 PLAN，MUST NOT 调用 `_apply_plan`（含复核器给出的 `revised_plan`），MUST 仅将有效 `fix_list` 注入教练提示。该修复期标志 MUST 写入断点以便续跑仍禁止 PLAN。证据门拒绝 MUST NOT 开启修复期。

#### Scenario: 有效 FAIL 不应用修订 PLAN

- **WHEN** 完成度复核返回有效 FAIL 且带有 `revised_plan`
- **THEN** 子任务清单不因该修订 PLAN 改变
- **AND** 步骤标题为「完成度复核未通过，请按修复清单执行」
- **AND** 教练提示包含有效修复清单、不含把修订 PLAN 写进状态

#### Scenario: 修复期内后续 PLAN 被丢弃

- **WHEN** 有效 FAIL 之后、下一 FINAL 被接受之前，模型输出 PLAN
- **THEN** 引擎不更新子任务清单
- **AND** 注入「不要重新规划、按修复清单执行」类提示

#### Scenario: 成功 FINAL 结束修复期

- **WHEN** 修复期内候选 FINAL 被接受（复核 PASS、空话 PASS 或连续有效 FAIL 收敛）
- **THEN** 清除修复期标志并按既有路径收尾

### Requirement: LLM 传输重试加强

当 LLM 请求发生传输错误（`httpx.TransportError`，含 `ConnectError`）时，引擎 MUST 在单次请求内重试最多 5 次，采用指数退避（约 2/4/8/16 秒）并加随机抖动；重试不改主循环逻辑。该重试 MUST 仅作用于传输错误，不作用于 HTTP 状态错误（400/422 等）。

#### Scenario: 传输错误指数退避重试

- **WHEN** LLM 请求因传输错误（如 `ConnectError`）失败
- **THEN** 引擎在单请求内重试（最多 5 次），退避逐次递增（约 2/4/8/16 秒）且带抖动
- **AND** 全部失败后才抛出传输失败异常，主循环逻辑不变

#### Scenario: HTTP 状态错误不进入传输重试

- **WHEN** LLM 请求返回 HTTP 400/422 等状态错误
- **THEN** 引擎不进入传输重试，按既有 HTTP 错误路径处理

### Requirement: 传输错误与 400 区分处理

传输错误（环境性、可自愈）与 HTTP 400（参数性、需修复）MUST 在运行退出策略上区分：传输错误连续 3 次才终止运行，HTTP 400 连续 2 次即终止。传输失败 / 400 的报错文案 MUST 包含端点信息（`llm.base_url`），便于排障。

#### Scenario: 传输错误连续 3 次才终止

- **WHEN** LLM 请求连续因传输错误失败
- **THEN** 连续 3 次传输失败才终止运行，而非与 400 共用 2 次阈值

#### Scenario: 400 连续 2 次终止

- **WHEN** LLM 请求连续因 HTTP 400 失败
- **THEN** 连续 2 次即终止运行，行为与现状一致

#### Scenario: 报错含端点

- **WHEN** LLM 请求因传输错误或 400 失败
- **THEN** 报错文案包含端点（`llm.base_url`）信息，便于定位故障

### Requirement: SQL 降轮次提示词强化

当 agent 绑定 MCP 时，系统提示 MUST 强化「互不依赖的 SQL / 查询必须同一轮批量输出、禁止一轮一条」的引导。该强化 MUST 仅为提示词软引导，不新增 SQL 清单落盘、回放执行或依赖回填机制；依赖型查询仍保持「观察后再继续」。

#### Scenario: 强化批量输出引导

- **WHEN** agent 绑定至少一个 MCP
- **THEN** 系统提示包含「互不依赖的 SQL 同一轮批量输出、禁止一轮一条」的强制措辞

#### Scenario: 不新增回放机制

- **WHEN** 模型输出多条互不依赖的 SQL
- **THEN** 引擎同轮串行执行（现有能力），不引入 SQL 清单落盘 / 回放 / 依赖回填

### Requirement: MCP 分页参数透出与同轮补齐引导

当 agent 绑定 MCP 时，引擎 MUST 在工具目录中透出 MCP 工具 `inputSchema` 的分页参数（offset/limit/page/page_size 等，含非 required 字段），使模型知道能分页；系统提示 MUST 引导模型「结果若被截断（返回行数 ≈ 所设 limit），同一轮用 offset 补齐剩余页」。引擎 MUST 不新增自动翻页逻辑——分页由模型自主发起，引擎仅按既有能力同轮串行执行多条 `MCP:`。

#### Scenario: 透出分页参数

- **WHEN** agent 绑定至少一个 MCP
- **THEN** MCP 工具目录包含其 `inputSchema` 的分页参数（offset/limit/page/page_size 等，含非 required 字段）
- **AND** 模型能在提示词中看到这些参数

#### Scenario: 同轮补齐引导

- **WHEN** 系统提示构建且 agent 绑定 MCP
- **THEN** 提示包含「结果被截断时同轮用 offset 补齐剩余页」的引导

#### Scenario: 不新增引擎自动翻页

- **WHEN** MCP 查询返回被截断的结果
- **THEN** 引擎不自动翻页、不注入 offset
- **AND** 分页完全由模型在提示词下自主发出多条 `MCP:`，引擎同轮串行执行（既有能力）

### Requirement: 交付附件标注引导

当 agent 可能产出需作为附件发送的文件时，系统提示 MUST 引导模型在 `FINAL:` 中标注 `attach=<path1,path2>`（或独立 `ATTACH:` 行）。该标注 MUST 仅为渠道层识别交付文件的约定，不影响 `FINAL:` 的其它语义；未标注 MUST 不报错。

#### Scenario: 引导标注附件

- **WHEN** 系统提示构建
- **THEN** 提示包含「产出文件需附件发送时，在 `FINAL:` 标注 `attach=<path>`（或独立 `ATTACH:` 行）」的格式说明

#### Scenario: 未标注不强制

- **WHEN** 模型在 `FINAL:` 未标注 `attach`
- **THEN** 引擎不报错，交由渠道兜底逻辑处理

### Requirement: 查询效率提示词强化

当 agent 绑定 MCP 时，系统提示 MUST 新增独立【查询效率】段，显式写入「优先用 WHERE/LIMIT 收窄、避免全表扫描、聚合（COUNT/SUM 等）在 SQL 侧完成、按总等待时间选最优查询」。该强化 MUST 仅为提示词软引导，不新增方案评估 / 成本模型等结构性机制。

#### Scenario: 独立查询效率段

- **WHEN** agent 绑定至少一个 MCP
- **THEN** 系统提示包含独立【查询效率】段，含 WHERE/LIMIT、避免全表扫描、聚合优先、按总等待时间选最优查询的显式引导

#### Scenario: 无结构性机制

- **WHEN** 模型选择查询方案
- **THEN** 引擎不评分、不选最优、不拦截，仅由提示词软引导

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

### Requirement: 无依赖同轮批量引导

当构建系统提示 / 工具目录 / coach hints 时，引擎 MUST 引导模型「无依赖的独立步骤可同轮输出多个工具调用」（多个 READ/SEARCH、多个独立 SHELL 等），而非「一次一个工具」；有依赖的步骤（后步需前步结果）MUST 仍分轮。引擎 MUST 不新增并行 / 聚合机制——同轮多工具由既有 `for step in tool_steps` 串行执行。

#### Scenario: 同轮批量引导

- **WHEN** 构建系统提示 / 工具目录 / coach hints
- **THEN** 措辞包含「无依赖的独立步骤可同轮输出多个工具调用」
- **AND** 不含「一次只输出一个工具」等相反引导

#### Scenario: 有依赖仍分轮

- **WHEN** 后续步骤依赖前一步骤的结果
- **THEN** 引导仍分轮，不鼓励依赖链同轮

#### Scenario: 不新增引擎机制

- **WHEN** 模型同轮输出多个工具调用
- **THEN** 引擎按既有 `for step in tool_steps` 串行执行，不新增并行 / 聚合逻辑

### Requirement: 大结果落盘回显

当 READ/SHELL 工具结果超过阈值（默认 4000 字符）时，引擎 MUST 将全文落盘到 `task/<ts>/`，上下文仅回显「路径 + 前 N 行预览 + 总长度」；模型 MUST 能按需用 READ/SEARCH 取回片段。SEARCH 结果已 cap N 条，MUST 不落盘。未超阈值 MUST 保持现状（原文入上下文）。

#### Scenario: 超阈值落盘

- **WHEN** READ/SHELL 结果字符数超过阈值（默认 4000）
- **THEN** 全文落盘到 `task/<ts>/`，上下文仅回显「路径 + 前 N 行预览 + 总长度」

#### Scenario: 未超阈值原文入上下文

- **WHEN** READ/SHELL 结果字符数未超过阈值
- **THEN** 结果原文入上下文（现状不变）

#### Scenario: SEARCH 不落盘

- **WHEN** 动作是 SEARCH
- **THEN** 结果不落盘，保持 cap N 条的现状

#### Scenario: 按需取回

- **WHEN** 模型需要落盘文件的内容
- **THEN** 模型可用 READ/SEARCH 按需取回片段

### Requirement: token 估算安全余量

`fit_messages_to_context` 计算 `allowed_out` 时 MUST 在现有 `ctx - used - reserve` 基础上追加安全余量，以弥补 `estimate_tokens`（≈ `len/2`）对 shell 输出 / 代码类内容的低估，避免 input+output 仍超窗触发 MiniMax 2013。

#### Scenario: 追加安全余量

- **WHEN** `fit_messages_to_context` 计算 `allowed_out`
- **THEN** 结果在现有封顶基础上再扣安全余量

#### Scenario: 长任务不超窗

- **WHEN** 多轮 shell/READ 的长任务运行
- **THEN** 不再触发 MiniMax 2013（input+output 稳定在窗口内）

### Requirement: 规划前置验证

当 `_apply_plan` 解析 PLAN 时，引擎 MUST 在本地（不额外调用 LLM）做静态前置检查：① 计划提到的动作（`SHELL:`/`READ:`/`WRITE:`/`SEARCH:`/`MCP:` 等协议前缀）MUST 都落在 `allowed_actions` 内；② 计划是否为空或无法解析出子任务。命中时 MUST 以软 `coach_hint` 提示，MUST NOT 阻断执行，MUST NOT 额外发起 LLM 调用。

#### Scenario: 越权动作软提示

- **WHEN** PLAN 正文提到某个协议动作且该动作不在 `allowed_actions`
- **THEN** 引擎追加一条 coach hint 提示「本 agent 无 <action> 权限」
- **AND** 不阻断执行、不额外调 LLM

#### Scenario: 权限内计划无提示

- **WHEN** PLAN 提到的动作都在 `allowed_actions` 内
- **THEN** 不追加前置验证提示，正常解析子任务

#### Scenario: 计划空或不可解析

- **WHEN** PLAN 为空或无法解析出子任务
- **THEN** 引擎追加一条 coach hint 提示使用带编号 / `[ ]` 清单的计划格式

### Requirement: 工具结果去重复用

当执行 READ / SHELL / SEARCH 动作时，引擎 MUST 先查去重缓存（去重键 = 动作 + 目标：READ 用路径、SHELL 用规范化命令、SEARCH 用 query）；命中且内容未变时 MUST 回显「已缓存，请引用之前结果」指针，MUST NOT 重读 / 重跑 / 把结果重新 push 进上下文。此缓存 MUST 与既有 MCP `query_cache` 形态一致（`{dedup_key: {path, tool, size}}`）。

#### Scenario: 命中回显指针

- **WHEN** 模型重复 READ 同一路径 / SHELL 同一命令 / SEARCH 同一 query，且内容未变
- **THEN** 引擎回显「已缓存，请引用之前结果」指针
- **AND** 不重新执行动作、不把结果重新 push 进上下文

#### Scenario: 未命中正常执行

- **WHEN** 动作 + 目标首次出现
- **THEN** 正常执行并把结果入上下文、写入去重缓存

### Requirement: 去重结果内容失效

READ 结果写缓存时 MUST 记「路径 + mtime/hash」；当 `file_write` 命中同一路径时 MUST 使该路径的 READ 缓存失效，避免文件被改写后仍回旧缓存。

#### Scenario: 改写后失效

- **WHEN** `file_write` 写入某路径，且该路径已存在 READ 缓存
- **THEN** 该路径的 READ 缓存失效，下次 READ 重新执行

#### Scenario: 未改写命中有效

- **WHEN** READ 缓存命中且路径的 mtime/hash 未变化
- **THEN** 返回缓存指针，不重读

### Requirement: 完成度复核交付物证据

`_reflect_final` 判定候选最终回复时，其 prompt MUST 附带 `saved_paths` 中每个交付物的「前 N 行内容摘要」（非仅路径名），使 Verifier 能核对实际数字 / SQL / 字段口径；MUST NOT 发起工具调用、MUST NOT 增加 LLM 轮次，MUST 保持纯裁判与 `_REFLECT_FAIL_CONVERGE=3` 收敛行为不变。

#### Scenario: 交付物摘要进入证据

- **WHEN** `_reflect_final` 构建判定 prompt
- **THEN** prompt 含 `saved_paths` 交付物的前 N 行内容摘要
- **AND** 不发起工具调用、不额外调 LLM

#### Scenario: 依据内容判 FAIL

- **WHEN** 候选回复的关键数字 / SQL / 字段口径与交付物内容不符
- **THEN** Verifier 判 FAIL 并给出修复清单

#### Scenario: 收敛行为不变

- **WHEN** 连续 FAIL 达 `_REFLECT_FAIL_CONVERGE`（3）
- **THEN** 接受候选、记录拒绝原因并收尾（现状不变）

### Requirement: 可重试 HTTP 状态码退避重试

当 LLM 请求返回可重试 HTTP 状态码（529 过载、429 限流、502/503/504 瞬态 5xx）时，引擎 MUST 做指数退避重试：529/429 重试 3 次（约 2s→6s→18s），5xx 重试 5 次，复用既有 `_post_with_transport_retry` 的 2/4/8/16s + jitter 骨架；确定性失败（400/401/2013/1026/1027）MUST NOT 重试，直接报错。组内降级（group failover）MUST 保持现状不改代码。

#### Scenario: 529/429 退避重试

- **WHEN** LLM 请求返回 529 或 429
- **THEN** 引擎按指数退避重试最多 3 次
- **AND** 每次重试之间有 sleep（约 2s→6s→18s）

#### Scenario: 5xx 退避重试

- **WHEN** LLM 请求返回 502/503/504
- **THEN** 引擎按指数退避重试最多 5 次

#### Scenario: 确定性失败不重试

- **WHEN** LLM 请求返回 400/401/2013/1026/1027
- **THEN** 不重试，直接报错

#### Scenario: 组内降级现状不变

- **WHEN** Agent 绑定 `type:"group"` 的 LLM 且某成员请求失败
- **THEN** 按既有 group failover 切换到下一成员，不新增健康度 / 轮换逻辑

### Requirement: 模型组成员校验

当创建 / 更新 `type:"group"` 的 LLM 资源时，引擎 MUST 校验其 `members`：每个成员 id MUST 对应已存在的 LLM 资源；每个成员 MUST 为 `type:"llm"`（叶子模型，禁止组套组）；成员列表 MUST NOT 包含组自身 id（禁止自引用）。任一违反时 MUST 返回可读错误并拒绝保存（不 commit）。`type:"llm"` 时 `members` MUST 清空 / 忽略。

#### Scenario: 成员不存在

- **WHEN** 保存 `type:"group"` 且某成员 id 在 LLM 资源中查不到
- **THEN** 返回可读错误，拒绝保存

#### Scenario: 成员自引用

- **WHEN** 保存 `type:"group"` 且 `members` 包含组自身 id
- **THEN** 返回可读错误，拒绝保存

#### Scenario: 成员为组（组套组）

- **WHEN** 保存 `type:"group"` 且某成员是 `type:"group"`
- **THEN** 返回可读错误，拒绝保存

#### Scenario: 叶子成员保存成功

- **WHEN** 保存 `type:"group"` 且所有成员均为存在且 `type:"llm"` 的叶子模型
- **THEN** 保存成功

### Requirement: 模型组解析环检测

`chat_completion` / `test_llm_chat` 解析 `type:"group"` 时 MUST 携带 `visited` 集合（按 LLM id）与深度上限（默认 8 层）；命中已访问 id 或超过深度上限时 MUST 抛出可读错误（如「模型组存在循环引用或嵌套过深」），MUST NOT 无限递归。单层叶子成员全部**真失败**（HTTP/传输/缺 choices 等）时 MUST 抛单层「模型组全部失败: <底层错误>」，不嵌套叠加前缀。空 HTTP 200 / 软空回 MUST NOT 视为成员失败。

#### Scenario: 自引用 / 循环引用组

- **WHEN** 解析的组存在自引用或 A→B→A 循环
- **THEN** 抛出「模型组存在循环引用或嵌套过深」
- **AND** 不无限递归、不产生上千层嵌套错误消息

#### Scenario: 嵌套过深

- **WHEN** 组嵌套层级超过深度上限
- **THEN** 抛出「模型组存在循环引用或嵌套过深」

#### Scenario: 单层成员全失败

- **WHEN** 组的所有叶子成员都真失败（如 529）
- **THEN** 抛出单层「模型组全部失败: <底层错误>」
- **AND** 错误消息不含重复叠加的「模型组全部失败」前缀

#### Scenario: 顺序切换语义不变

- **WHEN** 组某成员请求真失败
- **THEN** 仍按既有顺序切换到下一成员（不改变 group failover 语义）

#### Scenario: 空 200 不计入成员失败

- **WHEN** 组内某成员返回空 HTTP 200（无可执行产出，含抬预算重试后仍空）
- **THEN** 将该空回复作为成功返回
- **AND** 不切换下一成员、不抛「模型组全部失败」

### Requirement: 空 HTTP 200 软空回

当 LLM 返回 HTTP 200 且解析成功，但无可执行产出（无非空 content、无可映射的 native tool_calls，且非仅含 reasoning 字段的纯思考轮）时，`chat_completion` MUST NOT 抛错。系统 MUST 在同一请求内裁剪输入以抬高输出预算（目标 `allowed_out ≥ 2048`）并重试一次；仍无产出时 MUST 返回空回复给循环，循环 MUST 走既有空回复软提示路径继续。该结果 MUST NOT 计入 LLM 连续失败计数，MUST NOT 触发模型组切换到下一成员。

#### Scenario: 空 content 不 raise

- **WHEN** 叶子模型返回 HTTP 200、`content` 为空且无可执行 `tool_calls`、亦无 reasoning 兜底字段
- **THEN** 不抛「LLM 响应缺少 content」类错误
- **AND** 同请求内抬输出预算重试一次后仍空则返回空回复

#### Scenario: 空 200 不换组员

- **WHEN** Agent 绑定模型组且当前成员返回空 HTTP 200（含重试后仍空）
- **THEN** 将该空回复作为成功结果返回给循环
- **AND** 不切换到组内下一成员
- **AND** 不抛「模型组全部失败: LLM 响应缺少 content…」

#### Scenario: 纯思考轮仍软空回

- **WHEN** MiniMax 等返回仅含 reasoning 字段、无 content / tool_calls 的思考轮
- **THEN** 返回空回复且不计入 LLM 失败
- **AND** MUST NOT 仅为抬预算而强制重试（与既有软空回语义一致）

#### Scenario: 真错误仍 failover

- **WHEN** 成员返回 HTTP 4xx/5xx 或传输失败等真错误
- **THEN** 仍按既有模型组 failover 与 `llm_failures` 阈值处理

### Requirement: finish_reason=length 请求级续写

当响应 `finish_reason` 为 `length` 或 `max_tokens` 且已有可见文本时，`chat_completion` MUST 在同一请求内拼接 assistant 残篇并续写，最多 2 次；每次续写 MUST 保证足够输出预算（目标 `allowed_out ≥ 2048`）。续写成功（非 length）时 MUST 返回拼接后的完整文本。无 `finish_reason`（或非 length 类）且已有文本时 MUST NOT 启发式续写。

#### Scenario: length 续写拼接

- **WHEN** 首次响应 `finish_reason=length` 且 content 非空
- **THEN** 在同一次 `chat_completion` 内最多续写 2 次并拼接全文返回

#### Scenario: 无 finish_reason 不续写

- **WHEN** 响应已有文本但无 length / max_tokens 类 `finish_reason`
- **THEN** 不发起续写，按完整回复返回

#### Scenario: 续写成功返回全文

- **WHEN** 续写过程中某次响应不再是 length
- **THEN** 返回已拼接的完整文本，且不标记为截断

### Requirement: 截断输出阻断 FINAL

当请求级续写用尽后响应仍为 length 截断时，系统 MUST 将拼接文本交回循环并标记为输出截断。循环即使在该文本中识别到 `FINAL:` 或完成信号，MUST NOT 将其送入完成度复核或作为任务完成结束；MUST 注入软提示后继续下一轮。

#### Scenario: 截断残篇不得结束任务

- **WHEN** 续写 2 次后仍 `finish_reason=length` 且文本含 `FINAL:`
- **THEN** 循环不进入完成度复核、不将任务标为完成
- **AND** 注入「输出被截断」类软提示后继续

#### Scenario: 截断后下一轮可正常 FINAL

- **WHEN** 上一轮因截断被阻断，下一轮返回完整（非截断）FINAL
- **THEN** 按既有 FINAL / 复核路径正常结束

### Requirement: 残缺 native tool_calls 禁止执行

当 native `tool_calls` 因截断导致 JSON 残缺、或全部无法映射为可执行步骤时，系统 MUST 将其视同无可执行产出（走空响应软容错管道），MUST NOT 执行任何半截工具调用。

#### Scenario: 截断 tool_calls 不执行

- **WHEN** 响应含 `tool_calls` 但参数 JSON 截断或全部无法映射
- **THEN** 不执行任何工具
- **AND** 按空 / 截断容错路径处理（抬预算重试或软空回）

#### Scenario: length 截断的可解析 tool_calls 亦不执行

- **WHEN** `finish_reason=length` 且存在可映射的 tool_calls
- **THEN** MUST NOT 执行这些可能不完整的工具调用
- **AND** 按截断容错路径处理

### Requirement: 数据视图目录缓存与注入

引擎 MUST 缓存 MCP `list_ads_views` 的视图名清单（跨会话复用，带失效策略），并把该清单连同蒸馏出的 `view→字段/口径` 映射注入 task_context，使 PLAN 阶段能按语义一步匹配候选视图；模型 MUST 只对命中的 top 候选调用 `describe_ads_view` 确认字段，而非逐个 describe 全部视图。

#### Scenario: 视图名清单缓存

- **WHEN** 首次调用 `list_ads_views` 取得视图名清单
- **THEN** 结果缓存，跨会话复用，不每轮 / 每次运行重复列举

#### Scenario: 注入 task_context

- **WHEN** 构建任务上下文（PLAN 阶段）
- **THEN** 注入视图名清单及已蒸馏的 `view→字段/口径` 映射

#### Scenario: 一步语义匹配

- **WHEN** 具体需求到来
- **THEN** 模型按语义在视图清单中定位候选视图，`describe_ads_view` 只确认 top 候选
- **AND** 不逐个 describe 全部视图

### Requirement: 无进展破局复盘提示

当循环连续 N 轮（默认 5）无进展（无工具执行成功、无文件写入、无进度新增、无子任务推进）时，引擎 MUST 注入一条模板化「破局复盘」软提示（非 LLM 生成），内容含「已完成：<进度>；仍缺：<未完成子任务>；请二选一：调用工具推进，或输出 `FINAL: <当前结论>`」。该提示 MUST NOT 终止循环、MUST NOT 作为硬门禁；有推进时无进展计数 MUST 清零。

#### Scenario: 连续无进展注入破局提示

- **WHEN** 连续 5 轮无任何推进
- **THEN** 注入一条模板化破局复盘提示（含已完成 / 仍缺 / 二选一）
- **AND** 不终止循环、不提前收尾

#### Scenario: 有推进清零

- **WHEN** 任务有推进（文件写入 / 进度新增 / 子任务推进 / 工具成功）
- **THEN** 无进展计数清零，不注入破局提示

#### Scenario: 提示为软性非门禁

- **WHEN** 破局提示被注入
- **THEN** 循环仍由 `FINAL` / 用户取消 / LLM 错误 / `max_iters` 决定结束，提示本身不强制停止

### Requirement: 真实会话上下文可用百分比

引擎 MUST 计算「会话上下文可用百分比」= `(1 − 已用输入 token / 模型 max_context_tokens) × 100%`；其中「已用输入 token」MUST 用 `estimate_tokens(系统提示 + 历史消息 + 任务上下文 + 工具结果)` 求和，且在 `fit_messages_to_context` 裁剪前计算；结果 MUST 写入消息 meta 的 `context_available_percent`，替换硬编码 100% 回退。

#### Scenario: 真实可用率计算

- **WHEN** 一次运行结束后
- **THEN** 消息 meta 的 `context_available_percent` 为裁剪前计算出的真实可用率（非硬编码 100%）

#### Scenario: 随占用增长下降

- **WHEN** 系统提示 + 历史 + 任务上下文 + 工具结果的总 token 占用增长
- **THEN** 可用率相应下降，越接近窗口上限越趋向 0%

#### Scenario: 口径一致

- **WHEN** 计算可用率
- **THEN** 分子分母复用既有 `estimate_tokens` 与 `llm.max_context_tokens`，不与其它 token 口径割裂

### Requirement: 完成信号软转换

当模型连续 2 轮输出正向完成声明（含「已完成/任务完成/最终交付/已交付/无需再调用工具」等关键词、本轮无工具调用、非疑问句）时，引擎 MUST 调用一次 LLM 确认该文本是否为完成声明；确认后 MUST 将文本作为 `FINAL` 候选送入完成度复核（`_reflect_final`）。该转换 MUST 只认正向完成、MUST NOT 识别负向「无法完成/无法继续/无法连接」；MUST NOT 新增确定性停止门（循环仍由 `FINAL` / 用户取消 / LLM 错误 / `max_iters` 结束）。

#### Scenario: 连续两轮完成声明软转换

- **WHEN** 连续 2 轮输出正向完成声明文本且无工具调用
- **THEN** 引擎用一次 LLM 判断「是否为完成声明」
- **AND** 确认为完成声明后，把文本作为 `FINAL` 候选送入完成度复核

#### Scenario: 否定与疑问不触发

- **WHEN** 文本含「无法完成/不能/吗/？」等否定或疑问
- **THEN** 不触发软转换，仍走既有纯文本提示路径

#### Scenario: 转换后仍受复核约束

- **WHEN** 完成声明被软转换为 `FINAL` 候选
- **THEN** 仍经 `_reflect_final` 复核，FAIL 时按定向补处理

### Requirement: 需求感知完成度复核

完成度复核器 MUST 现场把任务目标（goal）逐条拆成核对项（列/字段/口径/补充说明），逐项判 PASS/FAIL；对数据准确性任务，字段口径、数字、映射关系 MUST 精确核对，仅排版与措辞可豁免（不得再以「小瑕疵」一律 PASS）。复核 FAIL 时 MUST 只定向补缺失项，保留已完成子任务与已写文件，不推翻完成态。

#### Scenario: 逐条核对

- **WHEN** 复核器收到 `FINAL` 候选
- **THEN** 逐条列出核对项并判 PASS/FAIL，FAIL 项进入修复清单

#### Scenario: 字段口径缺失判 FAIL

- **WHEN** 交付物缺失「渠道名称映射」等字段口径
- **THEN** 判 FAIL 并列入修复清单

#### Scenario: 定向补不推翻完成态

- **WHEN** 复核 FAIL
- **THEN** 修复清单只针对失败项，已完成子任务与已写文件保留

### Requirement: 已完成清单注入

引擎 MUST 每轮在任务上下文稳定层回显「已完成子任务（勾选）+ 已写文件路径 + 进度后 N 条 + 已尝试工具摘要」；resume 时额外注入「上次执行到此、还差什么」。该清单 MUST 由引擎零 LLM 拼（复用 progress_lines / saved_paths / subtasks / tool_call_tally / query_cache），MUST NOT 被 `trim_tool_results` 裁剪。

#### Scenario: 每轮回显清单

- **WHEN** 构建任务上下文（含 resume 后首轮）
- **THEN** 注入已完成清单（子任务 + 文件 + 进度 + 已尝试工具）

#### Scenario: 清单不被裁剪

- **WHEN** 上下文周期性裁剪
- **THEN** 已完成清单保留，不被裁剪

### Requirement: MCP 工具调用去重与大结果取回指引

所有 MCP 工具调用 MUST 按「工具名 + 归一化参数」作为 key 进入查询缓存；其中 `execute_ads_sql` 的参数按 SQL 归一化（去空白/大小写/尾分号，`LIMIT`/`OFFSET` 不同视为不同）；命中时 MUST 回显「该结果已缓存/已落盘 `path`，请 READ 取回，勿重跑」；结果过大落盘后 MUST 明确回显完整取回路径，而非只回显截断片段。

#### Scenario: 相同 MCP 工具调用命中缓存

- **WHEN** 模型再次调用与已执行 MCP 工具「工具名 + 归一化参数」相同的调用
- **THEN** 命中缓存，回显「已缓存/已落盘 `path`，请 READ 取回」

#### Scenario: 相同 SQL 命中缓存

- **WHEN** 模型再次调用与已执行 SQL 归一化后相同的 `execute_ads_sql`
- **THEN** 命中缓存，回显「已缓存/已落盘 `path`，请 READ 取回」

#### Scenario: 大结果落盘回显路径

- **WHEN** 结果过大落盘
- **THEN** 回显完整取回路径，供 READ/SHELL 取回

### Requirement: Runtime 在入口解析 Profile 而不改变 Standard 执行语义

统一 Runtime SHALL 在运行入口解析任务的显式 Profile，并将其编译为统一的执行上下文、能力集合与生命周期约束。对于未声明或选择 Standard Profile 的任务，Runtime SHALL 保持既有工具路由、运行行为、事件和终态语义。

#### Scenario: Standard Profile 与 Code Profile 并存

- **WHEN** 系统同时运行一个 Standard 任务和一个 Code Profile 任务
- **THEN** Standard 任务继续使用既有执行行为
- **AND** Code Profile 的专属上下文与能力仅作用于该 Code run

### Requirement: Code Profile 事件必须复用统一 Runtime 事件契约

Runtime SHALL 将 Code Profile 的准备、验证、审批/拒绝、封存和终结事件写入既有统一事件流，并允许携带版本化的 Profile 专属 payload。既有消费者 SHALL 能忽略未知 Profile payload 而继续处理通用事件字段。

#### Scenario: 旧事件消费者接收 Code run 事件

- **WHEN** 不识别 Code Profile 专属 payload 的既有消费者接收统一事件
- **THEN** 该消费者仍可读取通用事件字段
- **AND** 不因未知 Profile payload 失败

### Requirement: Code run 的终结必须进入统一清理生命周期

Runtime SHALL 将 Code run 的取消、超时、异常和正常结束纳入既有终结生命周期，并在结束时触发 Code Profile 所需的资源回收和审计封存。Code run 的清理失败 SHALL 产生可观察的基础设施异常。

#### Scenario: Code run 在工具执行中超时

- **WHEN** Code Profile 运行在工具执行过程中超时
- **THEN** Runtime 阻止新的工具动作并进入统一终结流程
- **AND** 记录资源回收或清理失败的结果

#### Scenario: 受管容器命令超过冻结 deadline

- **WHEN** 探索测试、验证基线或最终 Verifier 的受管容器命令超过该 run 的剩余冻结时限
- **THEN** 系统终止该命令并阻止新的执行动作
- **AND** run 以显式超时结果进入统一清理流程，而不是等待命令自行返回

#### Scenario: Runner 启动在 Workspace 准备后失败

- **WHEN** 系统已分配可写 Workspace，但 runner 在统一执行循环开始前启动失败
- **THEN** run 记录 `infrastructure_error` 并执行已分配资源的补偿清理
- **AND** 不保留可恢复执行的 `pending` run

### Requirement: No-progress hints are aggregated for diagnostics
The runtime MUST preserve the diagnostic value of repeated no-progress conditions while preventing one log line per loop iteration from dominating local runtime logs.

#### Scenario: Repeated no-progress hints are aggregated
- **WHEN** the same agent remains in a no-progress streak across repeated loop iterations
- **THEN** diagnostics expose the agent identifier, current iteration, streak length, and aggregate count or interval without emitting an unbounded identical log line per iteration

#### Scenario: Progress resets no-progress aggregation
- **WHEN** the runtime observes progress after a no-progress streak
- **THEN** the next no-progress diagnostic starts a new aggregate window rather than continuing the previous streak as if it were uninterrupted

#### Scenario: Aggregated hints remain operator-visible
- **WHEN** an operator reviews local runtime logs or log analysis output
- **THEN** they can still identify which agent is stuck and how long the no-progress streak has lasted

### Requirement: 复核缺口必须结构化且有新证据价值

完成度复核拒绝候选 FINAL 时，复核器 MUST 给出包含需求、缺失证据、未执行具体工具动作和判定条件的结构化缺口。运行时 MUST 验证字段、规范化动作签名及已有执行/物化证据；不完整、重复、已满足或可由缓存取回的缺口 MUST 不阻止 FINAL。

#### Scenario: 重复或散文式缺口不阻止 FINAL
- **WHEN** 复核只提出再检查，或其动作已执行、已缓存或已被现有证据满足
- **THEN** 运行时记录非阻塞备注并接受候选 FINAL

### Requirement: 每个有效缺口的取证次数有界

每个有效缺口 MUST 最多允许两次不同规范化签名的取证动作。耗尽后，运行时 MUST 不再重复该缺口的动作或推理；只读/分析任务 MUST 以标注条件、证据和未验证项的最终回复收尾。

#### Scenario: 第二次不同取证后以条件性结论收尾
- **WHEN** 只读缺口已完成两次不同取证且仍未满足判定条件
- **THEN** 运行时停止该缺口循环并输出条件性结论

### Requirement: 终结策略按实际动作风险分层

运行时 MUST 从实际工具动作判定风险。读取/查询和无副作用 Shell 为低风险；写入、删除、外发、部署、支付和无法判定的 Shell 为高风险。未解决高风险缺口 MUST 请求用户确认或授权，且 MUST NOT 自动执行或宣称完成。

#### Scenario: 高风险缺口请求确认
- **WHEN** 高风险缺口的取证已耗尽
- **THEN** 运行时输出已有证据、未完成项及确认/授权请求

### Requirement: 可路由 Standard 任务在首次模型调用前冻结选择

当 Standard/React Agent 绑定有效路由策略时，运行时 SHALL 在首次主模型调用前完成一次模型路由并冻结所选角色、叶子模型及策略版本。该冻结 MUST 供主循环、完成度复核、会话摘要与 MCP 语义路由复用；同一任务不得在正常轮次间重新路由。

#### Scenario: 任务内复用冻结模型

- **WHEN** 路由后的任务进入主循环并执行完成度复核或 MCP 语义路由
- **THEN** 所有这些 LLM 调用复用同一冻结叶子模型
- **AND** 不产生第二次任务分类或角色选择

### Requirement: 模型降级仅限于执行前的可恢复故障

运行时 SHALL 仅在冻结模型尚未生成有效响应、尚未产生原生 tool call 且尚未执行任何工具时，对网络失败、超时、限流、认证失效或熔断执行一次有界的有序降级。模型输出有效内容、请求业务澄清、生成工具调用或工具已执行后，系统 MUST NOT 自动切换模型并重跑该任务。

#### Scenario: 首次请求限流后降级

- **WHEN** 冻结首选模型在首次调用返回限流且未产生有效响应
- **THEN** 系统按角色模型组的有序降级模型重试一次
- **AND** 记录降级审计事件

#### Scenario: 工具执行后不自动重跑

- **WHEN** 已执行至少一个工具后模型调用失败
- **THEN** 系统不切换模型重放任务或工具调用
- **AND** 返回可续跑或可行动的失败状态
