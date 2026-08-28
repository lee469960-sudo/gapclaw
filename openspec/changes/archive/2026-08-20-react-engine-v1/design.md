## Context

本 change 合并两个在途 delta 的现状(动机见 proposal.md — Why):`react-engine`(FINAL 语义 + 文档 + SOP)与 `agent-runtime-resume-perf`(执行性能 + 续跑保真)。合并时发现二者的三条工作线九成正交,但共享同一批底层机制——`coach_hint` 单槽、`set_task_context` 的 plan 参数、checkpoint blob、MCP transport。因此 v1 把两套决策统一,并新增若干交叉裁决(见 D10–D14)。

关键现状:`coach_hint` 是 replace 语义(后写覆盖先写);`_run_modular` 是唯一主循环且 FINAL 分支在工具循环之前;`execute_action` 唯一生产调用方是 `_run_modular`,`call_mcp_tool` 另被 MCP 测试端点使用;checkpoint 只存 6 字段;`run_ts` 每轮重建。

## Goals / Non-Goals

**Goals:**

- 一套统一口径覆盖:FINAL 语义、执行性能、续跑保真、口径卫生、文档同步。
- 消除三类静默失败:FINAL+工具丢弃、多 MCP 只调第一个、多提示互相覆盖。

**Non-Goals:**

- 不引入硬门禁/计数器(软提示 > 硬门禁)。
- 不做跨运行 MCP 会话缓存。
- 不把 MCP 工具调用并行化(ReAct 循环保持顺序执行)。
- 不统一目录构建与执行两处 MCP 连接。

## Decisions

### D1: FINAL 优先契约不变,同轮工具跳过只加告警

在 FINAL 分支内(拿到 `final_step` 后、return/continue 前)计算 `skipped = [s for s in tool_steps if s.action != "plan" and not s.is_final]`,非空时注入可见步骤 + 软提示。不 reorder、不 parser 拒绝。理由:告警是调度层语义;解析层已把 step 拍平成 action 类型序,无法可靠重建意图。备选 reorder(改 FINAL 语义,连锁风险)与 parser 拒绝(反转契约)均否决。

### D2: 会话复用 = per-run 管理器,不建全局缓存

新增 `McpSessionManager`,由 `_run_modular` `async with` 包裹主循环体。per-run 优于全局:stdio 无泄漏、天然无并发(循环顺序执行)、无跨 run 污染;省的是「每调用 init」而非「每 run init」。全局缓存引入 stdio 泄漏/并发/污染三类风险,收益不成比例。

### D3: 会话穿透用可选参数,不动 `call_mcp_tool`

`execute_action` 加 `mcp_sessions: McpSessionManager | None = None`;非 None 走 `call_tool`,None 回退 `call_mcp_tool`(MCP 测试端点路径零影响)。理由:显式传参合代码风格;`execute_action` 唯一生产调用方是 `_run_modular`,加参改动面极小。备选 contextvar / 塞 `AgentContext` 均否决(隐式难测 / frozen 快照语义被污染)。

### D4: 会话失效恢复 = 重建 + 重试一次,分层于 transport 重试

复用引入两类新失败:stdio 子进程崩溃、streamable session-id 过期。恢复 = 丢旧会话 → 重新 initialize/spawn → 该次调用重试一次。分层于现有 transport 重试(瞬断/超时 backoff)之上,互不放大。备选「re-init 后复用 `_TRANSPORT_RETRY_MAX` 三次」会放大端点 down 时的重试,否决。

### D5: 目录连接与执行连接不合并

目录构建(`_get_mcp_tools_cached`,600s 模块级缓存)保持原样,会话复用只覆盖执行期。合并会破坏 600s **跨 run** 目录缓存(目录每 run 重拉,快速连续 run 更慢),且扩大穿透面。

### D6: checkpoint 扩展字段 + 向后兼容

`_save_run_state` payload 追加 `run_ts`/`mcp_results`(`[{seq, path, tool, args, size}]`)/`query_cache`(`{key: {path, tool, size}}`)/`plan_text`。`_load_run_state` 用 `.get()` 缺省空,旧 checkpoint 不报错。`run_ts` 有则复用、无则新生成;`mcp_result_seq` 续跑从 `len(mcp_results)` 恢复。

### D7: 查询去重的两层缓存生命周期(新增裁决)

去重有两条生命周期,必须分清:

- **in-run query_cache**(内存,per-run):去重主收益,无需 checkpoint。
- **cross-run query_cache**(checkpoint 持久化):仅长任务(≥2 子任务)续跑避免重查,受 LRU 条数 + 单条 size 上限约束,防 `AgentRunState.state` blob 膨胀。

去重语义归属 **per-run 编排层**,不属纯 transport:纯 transport 函数 `call_mcp_tool` 保持「一次调用 = 一次连接」语义不变(MCP 测试端点零影响);dedup 判定、落盘记账、`mcp_result_seq`(= `len(mcp_results)`)全部落在新增的 per-run `McpSessionManager`(它本就拥有 per-run 生命周期与 `mcp.id`,避免 `_run_modular` 再膨胀,且随 `async with` 覆盖关闭路径)。

### D8: 续跑上下文注入复用 `set_task_context` 的 plan 参数

续跑时 `cm.set_task_context(goal=..., plan=_render_subtask_list(state.subtasks) + "\n" + plan_text)`,产物路径回填 `progress_lines` 再 `set_progress_block`。不改 `context_manager.py` 结构。

### D9: 多 MCP 按工具名分派

`execute_action` MCP 分支改为遍历绑定 MCP,用工具目录(复用 `_get_mcp_tools_cached`)判断目标工具归属,命中才执行;无命中返回「未在绑定 MCP 中找到工具 X」。单 MCP 绑定行为不变。

### D10: Verifier 输入追加子任务清单 + 最近 progress

`_reflect_final` prompt 追加 `_render_subtask_list(state.subtasks)` 与最近 progress。无计数器/硬门禁。

### D11: 软提示聚合(新增架构基座,对应 C1)

`push_coach_hint` 是单槽 replace。引擎多类提示(FINAL+工具告警、卡死、失败/重复、子任务提醒、text-only)会在同一迭代并发产生。新增「聚合」:单迭代收集多条 hint → 带短标签 join 成一条 → 一次 push。**不**用「只留一条」的优先级(会静默丢信息,重蹈无声失败覆辙);优先级仅用于降噪排序。

### D12: SOP 迁出引擎 + 多 MCP 分派指引(对应 C2/C3)

业务口径(ads SOP、报表四要素、pandas/xlsx、依赖自装)迁到 Skill references;引擎目录只保留「真实工具名 + 描述 + required」的中性清单,并对每个工具标注来源 MCP 名称。严守 layer 边界:业务口径只进 Skill,运行时状态只进 task_context/progress_block/coach_hint,**禁止把 plan_text(可能含 view_map)回灌进引擎目录层**。多 MCP 场景下 Skill references 补「按工具归属 MCP 选 SOP」。

### D13: `close()` 异常吞掉 + 可观测补强(异常/可观测维度缺口)

`McpSessionManager.close()` 必须吞掉单会话关闭异常,不遮蔽主循环原始异常(`async with` 保证所有提前返回路径触发 close)。会话复用/失效/去重命中均产生可见步骤或日志,避免调试退化。

### D14: 文档按「事实陈述」重写

`docs/react-engine.md` 描述 v1 行为,标注易变阈值/别名为「以代码为准」,不夹带新约定。

## Risks / Trade-offs

- [风险] 会话复用后长 run 内 MCP 服务端状态跨调用残留 → 缓解:per-run 且短暂;去重只覆盖 query-like 工具,写类工具不受影响。
- [风险] 失效恢复与 transport 重试叠加 → 缓解:D4 明确 re-init 只重试一次。
- [风险] `mcp_result_seq` 续跑指向缺失文件(磁盘被清) → 缓解:`_materialize_mcp_result` 写失败返原文;引用悬空由模型 READ 报错后重查。
- [风险] 去重 key 规范化不足,语义同参数未命中 → 缓解:首版排序 JSON 覆盖「字段顺序」主因;语义等价留待验证。
- [风险] 软提示聚合使提示变长 → 缓解:降噪排序 + 每条带短标签;聚合上限条数,超限保留高优先级。
- [风险] SOP 迁出后「绑定 MCP 未绑定 Skill」agent 失去导出口径 → 缓解:目录留中性提示「详细导出 SOP 见已绑定 Skill 的 references」;迁移前 grep 存量绑定组合评估影响面。

## Migration Plan

1. **基座**:软提示聚合(D11)+ `close()` 异常吞掉 + 可观测步骤(D13)。—— 任一新增提示/执行逻辑之前必先落地。
2. **FINAL 语义**(D1):独立、低风险。
3. **MCP 执行层**(D2–D5、D9):会话复用 + 失效恢复 + 多 MCP 分派,不依赖 checkpoint。
4. **续跑层**(D6–D8、D10):checkpoint 扩展 + 续跑注入 + 查询去重 + Verifier 增强。
5. **SOP 迁移**(D12):依赖多 MCP 分派落定后,Skill references 补多 MCP 指引。
6. **docs 重写**(D14):最后,反映前 5 步最终形态。
7. **删除冗余代码**:扫描本次改动后失效的 helper。
8. 回滚:各阶段独立 commit,可分别 revert;checkpoint 扩展向后兼容。

## Open Questions

- 会话失效「re-init 重试一次」是否消耗现有 `_TRANSPORT_RETRY_MAX` 配额——倾向独立计数,实现时定死。
- 被跳过工具信息是否喂给 Verifier——倾向首版不喂,记入 observability 步骤。
- 去重 key 的语义等价是否首版覆盖——倾向不覆盖。
- cross-run query_cache 的 LRU 条数与单条 size 上限——依赖实测 blob 增长量。
- checkpoint blob 是否顺手加 `schema_version`——倾向加,成本极低。
