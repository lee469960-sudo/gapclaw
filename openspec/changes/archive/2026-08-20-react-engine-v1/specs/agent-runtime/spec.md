## Purpose

单 LLM 驱动的 ReAct 运行时,负责协议解析、工具执行、安全拦截、断点续跑与软性教练提示。本规格定义其 v1 行为契约:协议回复中 FINAL 与工具共现的处置、MCP 连接复用、checkpoint 保真、查询去重、续跑上下文注入、最终复核、多 MCP 分派,以及多软提示的合并呈现。

## ADDED Requirements

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

运行时 MUST 在单次运行内按 `(mcp_id, tool_name, 规范化参数)` 对 MCP 查询去重:命中已缓存结果时 MUST 不重调 MCP、不新增结果文件、不重复写入工具结果,仅返回指向已落盘结果的引用;未命中时 MUST 照常调用并按需落盘记入缓存。

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

### Requirement: 多 MCP 绑定正确分派工具调用

当 agent 绑定多个 MCP 时,工具调用 MUST 分派到实际声明了该工具的 MCP,而非固定返回第一个绑定的 MCP。

#### Scenario: 匹配工具所在 MCP

- **WHEN** agent 绑定多个 MCP,且目标工具仅存在于其中某一个 MCP 的目录中
- **THEN** 该工具在它实际所在的 MCP 上执行;单 MCP 绑定的行为不变

### Requirement: 多软提示在同一轮内合并而不互相覆盖

当同一迭代内产生多条软性教练提示(如 FINAL+工具告警、卡死提示、工具失败/重复提示、子任务提醒)时,运行时 MUST 将它们合并呈现,任一提示 MUST NOT 被后写的提示静默覆盖。

#### Scenario: 多提示同轮合并

- **WHEN** 同一迭代内同时产生多条不同来源的教练提示
- **THEN** 这些提示合并为一条完整呈现,无一条被覆盖丢失

#### Scenario: 单提示照常呈现

- **WHEN** 某迭代仅产生一条教练提示
- **THEN** 该提示正常呈现,行为与合并前一致
