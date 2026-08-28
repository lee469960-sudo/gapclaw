# Progress — react-engine-v1

> 实际完成情况记录。**唯一正式任务来源 = `tasks.md`**;本文件只跟踪状态与证据,不定义任务。
> 状态字典:`pending`(未开始)/ `in_progress`(进行中)/ `done`(完成)。

## 完成协议(强制)

1. 开始一个 task → 本文件标 `in_progress`。
2. **代码完成 + 对应测试通过 + 满足 acceptance criteria(spec Scenario / proposal What Changes)**,三者齐备 → 本文件标 `done`,并在「证据」列记录。
3. **然后**才在 `tasks.md` 勾选该 checkbox。
4. 仅代码修改、测试未跑或验收未满足的 task **不得**标 `done`,**不得**勾选 `tasks.md`。

## Phase 总览

| Phase | 标题 | tasks | 进度 |
|---|---|---|---|
| P1 | 软提示聚合 + 可观测基座 | 1.1–1.4 | 4/4 |
| P2 | FINAL 语义 | 2.1–2.2 | 2/2 |
| P3 | MCP 执行层 | 3.1–3.6 | 6/6 |
| P4 | 续跑层 | 4.1–4.8 | 8/8 |
| P5 | SOP 口径迁出 | 5.1–5.5 | 5/5 |
| P6 | docs 重写 | 6.1 | 1/1 |
| P7 | 冗余清理 + 收尾 | 7.1–7.3 | 3/3 |
| **合计** | | **29** | **29/29** |

## 任务明细

### P1 — 软提示聚合 + 可观测基座

| Task | 描述(tasks.md 为准) | 状态 | 证据(代码/测试/验收) |
|---|---|---|---|
| 1.1 | context_manager 新增 hint 聚合 | done | `context_manager.py` 新增 `_coach_hint_buffer`/`add_coach_hint`/`flush_coach_hints`;`runtime.py` 环内 9 处 `push_coach_hint`→`add_coach_hint`,环顶 `flush_coach_hints()`(line 686 初始提示保持 `push_coach_hint` 建立层)。测试 `test_context_manager_hint_aggregation.py` 4 条全绿 |
| 1.2 | close() 吞单会话异常 | done | `McpSessionManager.close()` 逐会话 try/except,单会话关闭异常只 `logger.warning` 不抛出。测试 `test_close_swallows_per_session_errors` 通过 |
| 1.3 | 复用/失效/去重命中可观测 | done | `_observe()` 写 `events` + `logger.info`:create / reuse / rebuild / materialize / dedup_hit 五类事件全覆盖。去重命中观测随 4.5 落地(`call_tool` 命中分支 `_observe("dedup_hit", …)`) |
| 1.4 | 测试:聚合不丢提示;close 无残留进程 | done | 聚合:`test_context_manager_hint_aggregation.py`(合并不丢/去重/空缓冲/空提示 4 条);close:`test_stdio_session_reused_across_calls` 断言 close、`test_stdio_failure_rebuilds_once_and_retries` 断言死会话被关闭、`test_close_swallows_per_session_errors` 断言不抛。共 8 条 session-manager 测试全绿 |

### P2 — FINAL 语义

| Task | 描述 | 状态 | 证据 |
|---|---|---|---|
| 2.1 | FINAL 分支 skipped 告警(经聚合层) | done | `runtime.py` FINAL 分支计算 `skipped=[非 FINAL 工具步骤]`,非空时 `_append_step` 注入 `final_skipped_tools` 可见步骤 + `cm.add_coach_hint("【FINAL 同轮工具】...")`(经聚合层,下一轮 flush) |
| 2.2 | 测试:FINAL 同轮三场景 | done | `tests/test_final_semantics.py` 3 条:SHELL+FINAL 告警且 SHELL 不执行;仅 FINAL 无告警;PLAN+FINAL 无非 PLAN 告警。全绿 |

### P3 — MCP 执行层

| Task | 描述 | 状态 | 证据 |
|---|---|---|---|
| 3.1 | McpSessionManager 复用会话 | done | `mcp_client.py` 新增 `McpSessionManager`/`_SessionBroken`/`_MCPHandle`/`_stdio_config`;按 `mcp.id` 缓存 stdio/streamable,legacy http 直调不缓存。测试 `test_stdio_session_reused_across_calls`(spawn 一次)、`test_streamable_session_initialized_once`、`test_legacy_http_is_not_cached` |
| 3.2 | 会话失效恢复(重建+重试一次) | done | stdio `RuntimeError`/`TimeoutError`、streamable transport 错误 → `_SessionBroken` → `call_tool` 丢旧会话重建并重试一次(分层于 `_call_streamable` 内 transport 重试)。测试 `test_stdio_failure_rebuilds_once_and_retries`(重建一次、死会话被 close) |
| 3.3 | execute_action 加 mcp_sessions 参数 | done | `execute_action(..., mcp_sessions=None)`;非 None 走 `mcp_sessions.call_tool`,None 回退 `call_mcp_tool`。测试 `test_execute_action_mcp_falls_back_to_call_mcp_tool_without_sessions` |
| 3.4 | _run_modular async with 包循环体 | done | `async with McpSessionManager() as mcp_sessions:` 包住主循环;`execute_action(..., mcp_sessions=mcp_sessions)`。runtime.py 语法/导入 OK |
| 3.5 | 多 MCP 按工具名分派 | done | `execute_action` MCP 分支遍历绑定 MCP 用 `_get_mcp_tools_cached` 按工具名匹配,无命中返回「未在绑定 MCP 中找到工具 X」。测试 `test_execute_action_dispatches_mcp_by_tool_name`、`test_execute_action_mcp_dispatch_no_hit_message` |
| 3.6 | 测试:复用/失效/close/分派 | done | `tests/test_mcp_session_manager.py` 8 条全绿;相关既有测试 44 条全绿 |

### P4 — 续跑层

| Task | 描述 | 状态 | 证据 |
|---|---|---|---|
| 4.1 | save 追加 run_ts/mcp_results/query_cache/plan_text | done | `_save_run_state` payload 追加四字段(runtime.py L1145–1148) |
| 4.2 | load 恢复四字段 + seq 恢复 | done | `_load_run_state` 用 `.get()` 缺省空恢复四字段(L1210–1213);`mcp_result_seq` 以 `len(mcp_results)` 恢复(McpSessionManager._materialize `seq = len(self.mcp_results)`) |
| 4.3 | run_ts 复用 + _apply_plan 持久化 plan_text | done | `run_ts = state.run_ts or …`(L604);`_apply_plan` 设 `state.plan_text = plan_text or ""`(L1254) |
| 4.4 | 续跑 set_task_context(plan) + progress 回填 | done | `set_task_context(goal, plan=_render_subtask_list + "\n\n" + plan_text)`(L642–648);回填 `mcp_results` path + `saved_paths` 进 progress(L677–687) |
| 4.5 | 查询去重(两层生命周期 + LRU) | done | `McpSessionManager.call_tool` 命中返引用、未命中 `_materialize` 记账;`query_cache` 由 runtime 传引用 = in-run 内存 + cross-run checkpoint 两层;`_trim_query_cache` FIFO 丢最旧(`max_cache_entries=100`)+ `max_cached_result_chars=2_000_000` 单条上限。见 F2 |
| 4.6 | 软子任务提示(经聚合层) | done | `_apply_plan` 内 `cm.add_coach_hint("【子任务推进】…重新输出带 [x] 的完整 PLAN")`(L1262–1264),经聚合层 flush |
| 4.7 | _reflect_final 输入增强 | done | `_reflect_final` prompt 追加 `subtask_block` + `recent_progress`(L478–479, L487–488) |
| 4.8 | 测试:roundtrip / resume / dedup | done | `test_long_task.py` 扩展四字段 roundtrip;新增 `test_resume_and_dedup.py` 3 条(`test_resume_injects_rendered_plan_into_context`、`test_query_cache_dedup_single_call_and_file`、`test_query_cache_lru_evicts_oldest`)。P4+session 相关 19 条全绿 |

### P5 — SOP 口径迁出引擎

| Task | 描述 | 状态 | 证据 |
|---|---|---|---|
| 5.1 | 确认 references 覆盖范围 | done | `ads-sync-hub/references/` 现有 `mcp-tools.md`(工具参考)+ `sync-sop.md`(第三方同步 SOP),均不覆盖报表/导出口径 → 决定**新增 `report-sop.md`**(不并入 sync-sop.md,二者是同步 vs 报表两条独立工作线) |
| 5.2 | 迁移 build_tools_desc 硬编码到 Skill | done | 新建 `references/report-sop.md` 承接「导出/查询策略」「报表 FINAL 四要素」「xlsx 生成示例」「依赖自装」;`SKILL.md` 指引更新为同时指向 report-sop.md |
| 5.3 | 删硬编码 + 目录中性化 + 标注 MCP | done | `system_prompt.py::build_tools_desc` 的 `if mcp_reachable:` ads 四段替换为中性指针「口径见已绑定 Skill 的 references」;「禁止为此再 query_ads_view」→「禁止为此再调用 MCP 数据源查询」;`utils._MCP_TOOL_EXAMPLES` 删 4 条 ads 条目、`_format_mcp_tools_for_prompt` 删 ads「硬规则」块,目录只剩「真实工具名 + 描述 + required」,来源 MCP 由 `- mcp {name}:` 前缀标注。`build_minimal_tools_desc` 本无 ads 硬编码,无需改动 |
| 5.4 | 多 MCP 分派指引入 Skill | done | `report-sop.md` 顶部新增「多 MCP 场景:按工具所属 MCP 选 SOP,ads 查询/报表走本文件,其他 MCP 走对应 Skill 的 references,不跨 MCP 套用口径」 |
| 5.5 | 更新测试断言(目录中性) | done | `test_mcp_catalog.py::test_format_mcp_tools_for_prompt_includes_sop_and_examples` → `..._is_neutral`:断言 `硬规则`/`SOP`/`MCP: list_ads_views {}`/`<待确认资源>` 等 ads 硬编码均不在,工具名与 `view_name`(required)仍在。全量 126 测试全绿 |

### P6 — 同步 docs/react-engine.md

| Task | 描述 | 状态 | 证据 |
|---|---|---|---|
| 6.1 | 重写为 v1 架构 | done | `docs/react-engine.md` 全量重写:模块表删除 `decision_engine.py` 等 5 个已删模块并注明删因,新增 `mcp_client.py`(McpSessionManager);`utils.py`/`system_prompt.py` 描述更新为「中性目录、SOP 在 Skill references」。§5 废弃硬门禁表(`_TEXT_ONLY_BREAK_AFTER`/`_DUPLICATE_BREAK_AFTER`/`_SAME_TOOL_BREAK_AFTER`)替换为 §5.4「硬停边界(仅三类)」;中文 FINAL 别名段改为「仅 `FINAL:`/`Final Answer:`」。新增/改写:`_reflect_final`(§5.2)、`_distill_final`(§5.3)、`_rescue_leaked_code`(§3)、native function-calling(§3.1)、断点续跑(§6)、会话复用(§7)、查询去重(§6.3)、软提示聚合(§4.1) |

### P7 — 冗余清理 + 验证收尾

| Task | 描述 | 状态 | 证据 |
|---|---|---|---|
| 7.1 | 删除失效 helper | done | 删除 `runtime.py` 两个死 helper:`_LARGE_MCP_RESULT_CHARS`(const)与 `_materialize_mcp_result`(func)。grep 确认仅定义处出现、无调用点/导入;落盘/去重语义已全部迁入 `McpSessionManager`。保留 `call_mcp_tool`(MCP 测试端点仍用)与 `_forced_stop_reply`(distill-fallback 仍用) |
| 7.2 | 全量测试全绿 | done | `cd apps/api && python -m pytest tests/ -q` → **126 passed**。`scripts/smoke_test.py` 为启动即发真实 HTTP 拉验证码的脚本(非测试),不属 pytest 范围,故以 `tests/` 目录为准;无新增静态门禁/阈值(仅 `soft_circuit` 作为提示文案参数,`text_only_streak>=2` 软提示,`llm_failures>=2` 硬停,均已在本次 scope 内) |
| 7.3 | openspec validate --strict | done | `openspec validate --strict react-engine-v1` → `Change 'react-engine-v1' is valid` |
