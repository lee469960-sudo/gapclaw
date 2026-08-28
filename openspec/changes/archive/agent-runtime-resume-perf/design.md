## Context

现状(动机见 proposal.md — Why):`_run_modular` 是唯一主循环,工具执行走 `execute_action` → `call_mcp_tool`。MCP 连接分两处:`tools/call` 每次重连(`_stdio_call_tool` 每次 spawn npx、`_streamable_call_once` 每次新建 httpx client + `initialize`),目录构建走 `_get_mcp_tools_cached`(模块级 600s 缓存)。checkpoint(`_save_run_state`)只存 6 个字段,不含 `run_ts`/`mcp_results`/`query_cache`/`plan_text`;`run_ts`(runtime.py:597)与 `mcp_result_seq`(598)每轮运行重建。续跑时 `cm.set_task_context(goal=...)`(635)不传 plan。`_reflect_final` 只喂 goal + memory + saved_paths。

爆炸半径(决定改法形态):`execute_action` 唯一生产调用方是 `_run_modular`(runtime.py:938);`call_mcp_tool` 另被 `routers/mcp.py:121`(MCP 测试端点,一次性语义)调用;`connect_mcp_detail` 被 `routers/mcp.py` 与 `utils._get_mcp_tools_cached` 调用。因此「会话复用」必须是**增量可选路径**,不能改 `call_mcp_tool` 的签名。

## Goals / Non-Goals

**Goals:**

- 单次运行内复用 MCP `tools/call` 会话,消除每调用重连/spawn。
- checkpoint 与续跑保真:续跑复用同一产物目录、恢复查询缓存与 PLAN 正文、注入子任务上下文。
- 相同查询去重,消除重复 `result_*.json` 与重复 push tool_result。

**Non-Goals:**

- 不统一「目录构建」与「执行」两处连接(目录保留 600s 全局缓存,见 D4)。
- 不做跨运行(multi-run)会话缓存——stdio 泄漏、并发、session 污染代价不成比例。
- 不引入硬门禁/计数器;缓存 LRU 上限只是内存卫生。
- 不把 MCP 工具调用并行化(ReAct 循环保持顺序执行,见 D1 的并发说明)。

## Decisions

### D1: 会话复用以「per-run 管理器」承载,不建进程全局缓存

新增 `McpSessionManager`,由 `_run_modular` 创建、`async with` 包裹主循环体、随运行结束统一 close。选择 per-run 而非全局缓存:

- **stdio 泄漏**:per-run 结束即 kill,全局缓存需引用计数/TTL,否则累积 npx 子进程。
- **并发安全**:ReAct 循环顺序执行工具,单运行内天然无并发;per-run 下不同运行各持一份,互不共享 stdio 单缓冲区的 NDJSON 通道。全局缓存会让两个 agent 共享一个 stdio 子进程,非并发安全。
- **跨 run 污染**:session-id / 服务端游标不复用,无跨 run 状态。
- **收益边界**:省的是「每调用 init」,不是「每 run init」;per-run 已覆盖 item 1 的靶点。

备选(全局缓存)在 `_QUERY_LIKE_TOOLS` 的 streamable 场景下能多省一次 per-run init,但引入上述三类风险,放弃。

### D2: 会话穿透用「可选参数」,不动 `call_mcp_tool`

`execute_action` 增加可选 `mcp_sessions: McpSessionManager | None = None`:

- 非 None → 走 `mcp_sessions.call_tool(mcp, tool, args)`(复用)。
- None → 走现有 `call_mcp_tool`(一次性,MCP 测试端点路径,零影响)。

理由:代码库风格是显式传参(`db`/`agent`);`execute_action` 唯一生产调用方是 `_run_modular`,加参改动面 = 1 处生产代码 + 测试。备选:放 `AgentContext`(frozen 语义是「不可变调用快照」,混入运行时可变对象不干净);contextvar(隐式、难测、违背显式风格)。均不取。

### D3: 会话失效恢复 = 「重建 + 重试一次」,与 transport 重试分层

复用会话引入两类新失败:stdio 子进程崩溃(`proc.returncode` 非空)、streamable session-id 过期。恢复策略**分层**于现有 transport 重试之上:

- **transport 重试**(现有):`_QUERY_LIKE_TOOLS` 的瞬断/超时,backoff 重试,保留原样。
- **会话失效恢复**(新增):检测到会话已死/过期 → 丢旧会话 → 重新 `initialize`/spawn → 该次调用重试一次。不重试多次(会话失效不是瞬断,re-init 后仍失败说明端点本身 down,交回 transport 重试逻辑)。

备选「re-init 后复用 `_TRANSPORT_RETRY_MAX` 三次」会导致「端点 down 时每次重试都重新握手」,开销放大且无收益。legacy http 无会话概念,绕过管理器每次直调,不缓存。

### D4: 目录连接与执行连接不合并

`tools/call` 会话复用只覆盖执行期;目录构建(`_get_mcp_tools_cached`,600s 模块级缓存)保持原样。合并(让 manager 从 run 开始就服务目录 + 执行)会:(a) 把 manager 穿透进 `build_tools_desc`/`_get_mcp_tools_cached`,改动面扩大;(b) 破坏 600s **跨 run** 目录缓存——目录每 run 重拉,快速连续 run 反而更慢。目录缓存(跨 run)与执行会话(per-run)生命周期本就不同,不合并。

### D5: checkpoint 扩展字段 schema 与向后兼容

`_save_run_state` payload 追加:`run_ts`(str)、`mcp_results`(`[{seq, path, tool, args, size}]`)、`query_cache`(`{key: {path, tool, size}}`)、`plan_text`(str)。`_load_run_state` 恢复时用 `.get(...)` 缺省空值——旧 checkpoint 无这些字段按空处理,不破坏兼容。`run_ts` 生成逻辑改为:加载的 checkpoint 有 `run_ts` 则复用,否则 `str(int(time.time()*1000))`。`mcp_result_seq` 续跑时从 `mcp_results` 长度恢复,避免覆盖已有 `mcp_result_N.json`。

### D6: 查询去重 key 与 LRU 上限

key = `(mcp_id, tool_name, 规范化 args)`,规范化 args = 排序后的 JSON(键稳定,忽略无意义差异如空串/None)。命中即返回「已缓存,结果见 `task/<ts>/mcp_result_N.json`」引用,不重调 MCP、不落新文件、不重复 push tool_result(在 `_materialize_mcp_result` 之前短路)。未命中照常调用,大结果落盘后记入 `mcp_results` + `query_cache`。LRU 上限仅内存卫生(按条数,超出丢最旧),**不是**循环硬门禁——超限只是丢掉旧缓存让下次重查,不打断循环。

### D7: 续跑上下文注入复用 `set_task_context` 的 plan 参数

续跑时(`resumed=True`)调用 `cm.set_task_context(goal=..., plan=_render_subtask_list(state.subtasks) + "\n" + plan_text)`,让 `[x]`/`[ ]` 与 view_map 进入每轮上下文。`mcp_results` 的 path + `saved_paths` 回填进 `state.progress_lines`,再 `cm.set_progress_block`。不改 `context_manager.py` 结构(plan 参数已存在)。

### D8: 多 MCP 分派改为按工具名匹配

`execute_action` 的 MCP 分支当前 `for mid in mcp_ids: if mcp: return ...` 命中第一个存在 MCP 即 return。改为遍历绑定 MCP,用该 MCP 的工具目录(已有 `_get_mcp_tools_cached`)判断目标工具是否在其中,命中才执行;无命中则返回「未在绑定 MCP 中找到工具 X」。单 MCP 绑定行为不变。

### D9: Verifier 输入追加子任务清单 + 最近进度

`_reflect_final` prompt 追加两段:`_render_subtask_list(state.subtasks)` 与最近 progress(复用 `_build_stuck_hint` 的 progress 回放写法)。复核判定仍无计数器/硬门禁。

## Risks / Trade-offs

- [风险] 会话复用后,一个长 run 内 MCP 服务端状态(游标/临时态)跨工具调用残留 → 缓解:复用是 per-run 且短暂;查询类工具本就无副作用,副作用工具(写类)不受 key 去重影响(去重只覆盖 query-like 工具,见 D6 实施时限定)。
- [风险] 会话失效恢复与 transport 重试叠加,极端下每次调用重试次数变多 → 缓解:D3 明确 re-init 只重试一次,不放大 transport 重试。
- [风险] `mcp_result_seq` 续跑从 `mcp_results` 长度恢复,若 checkpoint 与磁盘不同步(文件被清)会指向缺失文件 → 缓解:`_materialize_mcp_result` 已对写失败返回原文;续跑时对 `mcp_results` 只做引用回填,实际读取仍由模型 `READ:` 触发,读不到时自然报错可重查。
- [风险] 去重 key 规范化不够,语义相同但写法不同的 args 未命中 → 缓解:首版做排序 JSON 规范化,覆盖「字段顺序不同」这一主因;语义级等价(同义词)不追求,记为 Open Question。
- [风险] 多 MCP 分派依赖工具目录可用,目录未缓存时多一次 `tools/list` → 缓解:复用 `_get_mcp_tools_cached` 的 600s 缓存,不引入额外开销。

## Migration Plan

1. 先合「会话复用 + 失效恢复 + 多 MCP 分派」(MCP 层,独立可 revert)。
2. 再合「checkpoint 扩展 + 续跑上下文注入」(续跑层,含 roundtrip/注入测试)。
3. 再合「查询去重 + 软子任务提示 + Verifier 输入增强」(去重与提示,依赖 2 的 checkpoint 字段)。
4. 最后「删除冗余代码」扫描。
5. 回滚:各工作线独立 commit,可分别 revert;checkpoint 扩展向后兼容,旧 checkpoint 不报错。

## Open Questions

- 去重 key 的「语义等价」(同义参数字面不同)是否要在首版覆盖——倾向不覆盖,留待真实重复查询数据验证后再决定。
- `mcp_results`/`query_cache` 是否需要落盘为独立文件而非塞进 `AgentRunState.state` JSON blob——当前 blob 方案已够(单行 JSON,量级小),若实测变大再拆。
