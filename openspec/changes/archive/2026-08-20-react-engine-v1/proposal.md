## Why

单引擎 ReAct 经过上一代 consolidation 后已稳定,但「多轮长任务」路径仍有三个已确认的缺陷簇:引擎内部文档与代码脱节、业务 SOP 硬编码进引擎违反自身「业务口径写 Skill」原则、以及多轮执行/续跑的性能与保真缺陷(MCP 每次调用重连、checkpoint 不保真、相同查询重复落盘、续跑不注入子任务上下文、多 MCP 只调第一个、FINAL 与工具同轮静默丢弃工具)。本 change 把在途的 `react-engine` 与 `agent-runtime-resume-perf` 两个 delta 合并为一个统一的 v1 方案,一并落地。

## What Changes

1. **FINAL + 工具同轮静默丢弃防护(行为变更)**
   - 现状:单轮回复同时含 `FINAL` 与任一非 `PLAN` 工具步骤时,主循环在工具执行循环之前命中 FINAL 分支,被丢弃的工具既不执行也不告警。
   - 变更:保留 FINAL 优先契约,但注入**一条软性教练提示 + 一条可见步骤告警**,点明「本轮 FINAL 与工具同时出现,工具未执行」。不改 FINAL 优先语义,不加硬门禁。

2. **MCP 会话复用(治「拿数据慢」)**
   - 单次运行内,同一 MCP 的 streamable 连接 `initialize` 一次、多次 `tools/call`;stdio 子进程复用同一 npx 进程。会话随运行结束统一关闭(含取消/异常等提前返回路径)。复用后新增会话失效恢复:stdio 子进程崩溃或 streamable session-id 过期时,重建会话并重试一次(叠加在现有 transport 重试之上)。

3. **checkpoint 扩展 + 续跑上下文注入(治「续跑孤产物 + 0/6 无精度推进」)**
   - `_save_run_state` payload 追加 `run_ts`、`mcp_results`、`query_cache`、`plan_text`;`run_ts` 首轮生成、落 checkpoint,续跑复用同一 `task/<ts>/`,不再孤立旧产物。
   - 续跑时 `set_task_context(goal, plan=渲染子任务清单 + plan_text)` 让 `[x]`/`[ ]` 与 view_map 进入上下文,并回填 `mcp_results` + `saved_paths` 进 progress_block。

4. **查询缓存 + 去重(治「重复 result_*.json」)**
   - `mcp_tool_call` 执行前算 `key = (mcp_id, tool_name, 规范化 args)`;命中直接返回「已缓存,结果见 path」,不重调、不落新文件、不重复 push tool_result。未命中照常调用并记账。缓存分两层生命周期:in-run(内存)与 cross-run(checkpoint 持久化,受 LRU + 大小上限约束)。

5. **软子任务提示** — 子任务推进时 coach hint 提醒「完成子任务后重发带 `[x]` 的完整 PLAN」,无推断/计数。

6. **Verifier 输入增强** — `_reflect_final` prompt 追加「渲染后的子任务清单 + 最近 progress」,使 FINAL 裁判按子任务完成度判定。

7. **多 MCP 不再只调第一个(修复既有 bug)** — `execute_action` 的 MCP 分支改为按工具名匹配,多 MCP 绑定的 agent 不再静默失效。

8. **软提示聚合(新增架构基座)** — 引擎多类 coach hint(FINAL+工具告警、卡死、失败/重复、子任务提醒等)共享单槽 replace 语义,后写覆盖先写。新增「单迭代收集多条 hint → join → 一次 push」的聚合机制,避免任一提示被静默吞掉。

9. **SOP 口径迁出引擎(可观察行为变化)** — `system_prompt.py` 中硬编码的 ads 视图 SOP、报表 FINAL 四要素、pandas/xlsx 示例、依赖自装迁到对应 Skill 的 `references/*.md`,引擎只保留中性引导;**多 MCP 场景下 Skill references 补「按工具归属 MCP 选 SOP」**。注意:「绑定 MCP 但未绑定对应 Skill」的 agent 不再自动获得 ads 导出口径(有意收口)。

10. **同步 `docs/react-engine.md`(纯文档)** — 删除已合并/删除的模块与废弃的硬门禁表、中文 FINAL 别名,补记 `_reflect_final`/`_distill_final`/`_rescue_leaked_code`/native function-calling/断点续跑/会话复用/去重等,作为 v1 的维护地图。

11. **删除冗余代码** — 扫描并删除本次改动后失效的 helper(无静态硬门禁、无新增阈值/计数器)。

> 本 change **取代**在途的 `react-engine` 与 `agent-runtime-resume-perf`:两个 delta 的全部工作线并入此处,后续应归档/废弃那两个 change。

## Capabilities

### New Capabilities

- `agent-runtime`: 单 LLM 驱动的 ReAct 运行时。本次在该能力下新增 9 条需求——FINAL 与工具同轮告警、MCP 会话复用、checkpoint 保真、查询去重、续跑上下文注入、软子任务提示、Verifier 输入增强、多 MCP 分派、软提示聚合。该能力路径与两个前置 change 引入的 `agent-runtime` 相同(三者都是 delta,归档时合并进同一 `openspec/specs/agent-runtime/`)。

### Modified Capabilities

(无——`openspec/specs/` 当前为空,无既有能力规格可修改。)

## Impact

- **代码**
  - `apps/api/app/services/mcp_client.py`:新增 per-run 会话管理器(复用 stdio/streamable 会话 + 失效恢复);`call_mcp_tool` 保持原样(供 MCP 测试端点一次性调用)。
  - `apps/api/app/services/agent_tools.py`:`execute_action` 增加可选 `mcp_sessions` 参数;MCP 分支按工具名分派(多 MCP)。
  - `apps/api/app/services/agent_runtime/runtime.py`:FINAL 分支告警 + 软提示聚合;`_save_run_state`/`_load_run_state` 扩展 payload;续跑注入 plan;查询去重与 `mcp_results` 记账;`_reflect_final` 输入增强;`_apply_plan` 软提示。
  - `apps/api/app/services/agent_runtime/system_prompt.py` + `utils.py`:删除硬编码业务 SOP,替换为中性引导。
  - `apps/api/app/services/agent_runtime/context_manager.py`:新增 hint 聚合能力(单槽收集多条再 push)。
  - Skill `references/*.md`:承接被迁出的业务口径(含多 MCP 分派指引)。
- **文档**:`docs/react-engine.md` 全文重写为 v1 架构。
- **测试**
  - 新增:FINAL+工具告警、会话复用/失效恢复/close-on-cancel、多 MCP 分派、`test_query_cache_dedup`、`test_resume_injects_context`、hint 聚合不丢提示。
  - 扩展:`test_run_state_roundtrip`(四字段 roundtrip 保真);SOP 迁出后目录中性断言。
- **兼容性**
  - 全部为增量或缺陷修复,无 BREAKING。checkpoint 新字段向后兼容(旧 checkpoint 缺字段按空处理);`call_mcp_tool`/`routers/mcp.py`/目录构建零改动。
  - SOP 迁出与多 MCP 修复是**可观察行为变化**(前者对未绑定 Skill 的 agent 失去隐式 SOP;后者修复多 MCP 静默失效),单 MCP 绑定行为不变。
