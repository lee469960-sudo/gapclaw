## Why

单引擎 ReAct 稳定后,「多轮长任务」路径仍有三个已确认的缺陷拖慢执行或让续跑退化:(1) MCP 连接每次 `tools/call` 都重新握手——streamable 每次重 `initialize`,stdio 每次重 spawn npx 子进程,拿数据慢;(2) 断点续跑的 checkpoint(`_save_run_state`/`_load_run_state`)不含 `run_ts`/`mcp_results`/`query_cache`/`plan_text`,续跑后孤立旧产物、重查已查过的数据、丢失 PLAN 正文里的 view_map,且续跑时 `set_task_context` 不注入子任务清单,导致「0/6 无精度推进」;(3) FINAL 完成度复核器 `_reflect_final` 只看 goal + saved_paths,看不到子任务完成度,误判概率高。另有「多 MCP 绑定只调用第一个」的既有 bug。

## What Changes

1. **MCP 会话复用(治「拿数据慢」)** — 单次运行内,同一 MCP 的 streamable 连接 `initialize` 一次、多次 `tools/call`;stdio 子进程复用同一 npx 进程。会话随 `_run_modular` 生命周期结束统一关闭(含取消/异常等所有提前返回路径)。复用后新增会话失效恢复:stdio 子进程崩溃或 streamable session-id 过期时,重建会话并重试一次(叠加在现有 transport 重试之上)。

2. **checkpoint 扩展(治「续跑孤产物 + 重查」)** — `_save_run_state` payload 追加 `run_ts`、`mcp_results`(`[{seq, path, tool, args, size}]`)、`query_cache`(`{key: {path, tool, size}}`)、`plan_text`(原始 PLAN 正文,承载 view_map)。`run_ts` 首轮生成、落 checkpoint;续跑复用同一 `task/<ts>/`,不再孤立旧产物。

3. **查询缓存 + 去重(治「重复 result_*.json」)** — `mcp_tool_call` 执行前算 `key = (mcp_id, tool_name, 规范化 args)`。命中 → 直接返回「已缓存,结果见 task/<ts>/mcp_result_N.json」,不重调 MCP、不落新文件、不重复 push tool_result。未命中 → 照常调用,大结果落盘后记入 `mcp_results` + `query_cache`。缓存上限仅作内存卫生(LRU 按条数,超出丢最旧),非循环硬门禁。

4. **续跑上下文注入(治「0/6 无精度推进」)** — 续跑时 `set_task_context(goal, plan=渲染子任务清单 + plan_text)`,让 `[x]`/`[ ]` 与 view_map 真正进入上下文;恢复 `mcp_results` + `saved_paths` 回 progress_block,模型据此从首个 pending 续、跳过已确认的 view。

5. **软子任务提示** — 子任务推进(`_apply_plan` 或 progress 更新)时 coach hint 提醒「完成子任务后重发带 `[x]` 的完整 PLAN」。无任何推断/计数。

6. **Verifier 输入增强** — `_reflect_final` prompt 追加「渲染后的子任务清单 + 最近 progress」,使 FINAL 裁判能按子任务完成度判定。

7. **多 MCP 不再只调第一个(修复既有 bug)** — `agent_tools.execute_action` 的 MCP 分支当前 `for mid in mcp_ids: if mcp: return ...`,找到第一个存在的 MCP 就 return。改为找到匹配该工具名的 MCP 再执行;多 MCP 绑定的 agent 不再静默失效。

8. **删除冗余代码** — 扫描并删除本次改动后失效的 helper(无静态硬门禁、无新增阈值/计数器)。

## Capabilities

### New Capabilities

- `agent-runtime`: 单 LLM 驱动的 ReAct 运行时。本次在该能力下新增需求——MCP 会话复用、断点续跑 checkpoints 保真、查询去重缓存、续跑上下文注入、软子任务提示、Verifier 输入增强、多 MCP 正确分派。该能力路径与在途 change `react-engine` 引入的 `agent-runtime` 相同(两者都是 delta,归档时合并)。

### Modified Capabilities

(无——`openspec/specs/` 当前为空,无既有能力规格可修改。)

## Impact

- **代码**
  - `apps/api/app/services/mcp_client.py`:新增 per-run 会话管理器(`McpSessionManager`),复用 stdio/streamable 会话并处理失效恢复;`call_mcp_tool` 保持原样(供 MCP 测试端点 `routers/mcp.py` 一次性调用)。
  - `apps/api/app/services/agent_tools.py`:`execute_action` 增加可选 `mcp_sessions` 参数;MCP 分支改为按工具名匹配 MCP(修复多 MCP)。
  - `apps/api/app/services/agent_runtime/runtime.py`:`_run_modular` 创建/传递/关闭会话管理器;`_save_run_state`/`_load_run_state` 扩展 payload;`set_task_context` 续跑注入 plan;`_materialize_mcp_result` 与 query cache 记账;`_reflect_final` prompt 追加子任务 + progress;`_apply_plan` 软提示。
  - `apps/api/app/services/agent_runtime/context_manager.py`:无结构变化(复用 `set_task_context` 的 plan 参数)。
- **测试**
  - 新增:`test_query_cache_dedup`(相同查询只 call 一次、只一个 result 文件)、`test_resume_injects_context`(续跑重建 state 且 task_context 含子任务 + plan)、会话复用/失效恢复测试、多 MCP 分派测试。
  - 扩展:`test_run_state_roundtrip`(run_ts/mcp_results/query_cache/plan_text roundtrip 保真)。
- **兼容性**
  - 全部为增量或缺陷修复,无 BREAKING。checkpoint 新字段向后兼容(旧 checkpoint 缺字段按空处理);`call_mcp_tool`/`routers/mcp.py`/目录构建(`_get_mcp_tools_cached`)零改动。
  - 多 MCP 修复是**可观察行为变化**(此前第二个及以后 MCP 被静默跳过),属缺陷修复,对单 MCP 绑定 agent 无影响。
