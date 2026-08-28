# Findings — react-engine-v14（执行中记录）

记录执行过程中发现的问题、张力与决策。与 `progress.md` 互补：本文件记录「问题/决策」，`progress.md` 记录「完成事实」。

## F1 — 任务总数实为 18，非 17（已修正）

- **问题**：`task_plan.md` / `progress.md` 初稿写「17 个 task」，但 `tasks.md` 实际 18 个（1.1–1.4 / 2.1–2.3 / 3.1–3.2 / 4.1–4.4 / 5.1–5.5 = 4+3+2+4+5=18）。
- **决策**：以 `tasks.md`（唯一正式任务来源）为准，修正为 18。

## F2 — R3 视图目录缓存的落点（design 开放问题 D7 的取舍）

- **问题**：design.md 将「视图目录缓存落点（MCP 级 vs agent 级）」列为开放问题。
- **决策**：落在 **MCP 级**，`key = mcp_id`。缓存在 `agent_tools.execute_action` 的 MCP 分发点（拿到 `target` 与真实 `result` 后）调用 `record_ads_view_catalog(target.id, tool, args, result)`，而不是在 `runtime.py` 主循环里钩（主循环拿不到「哪个 MCP 执行了 list_ads_views」——那是在 `execute_action` 内部解析的）。复用既有 tools/list TTL 缓存模式（模块级 dict + `time.monotonic` + TTL），不另起并行缓存。

## F3 — R3「view→字段/口径 映射」的来源（4.2 的解释）

- **问题**：spec 要求「蒸馏出的 view→字段/口径 映射持久化复用」，但设计未指明映射从哪来、何时被「蒸馏」。
- **决策**：映射 = `describe_ads_view`（及同类 describe 工具）返回结果的字段结构摘要，经 `_json_keys_summary`（既有 mcp_client 工具）蒸馏，以 `{view: 结构摘要}` 缓存，key = mcp_id。视图名清单（4.1）来自 `list_ads_views` 结果。二者都在下次运行经 `build_ads_view_catalog(mcp_ids)` 合并注入 task_context。
- **局限**：映射不含人工「一句话语义描述」——当前只有字段结构摘要（与 design 开放问题「当前只有视图名，无一句话语义」一致）。这是一致但更弱的语义，已在 `progress.md` 标注。

## F4 — R3 工具名集合是「软提示」而非门禁

- **问题**：`list_ads_views` / `describe_ads_view` 工具名可能因 MCP 而异，硬编码会漏缓存。
- **决策**：用一组宽松的「list/describe 类工具名」集合（`list_ads_views`/`list_views`/…、`describe_ads_view`/`describe_view`/…）做 best-effort 识别；不命中就不缓存，绝不 gate。`_looks_like_tool_error` 挡掉失败/空结果，避免把错误串当目录缓存。

## F5 — R2 语义变化导致 2 个 legacy 测试需要改为长任务

- **问题**：`tests/test_react_engine_v5.py` 的 `test_native_skipped_backfills_final_first_result` 与 `test_reject_convergence_and_fix_list_backfill` 用「裸 `FINAL: 完成`（无子任务/交付物）」触发 `_reflect_final`。R2 落地后，这类短任务按 spec **跳过** 复核，直接收尾，导致这两个测试（分别断言二次复核轮与 3 次拒绝收敛）失效。
- **决策**：这不是回归，是 R2 的预期语义变化。给这两个测试 `_save_run_state` 预置 subtasks（变成长任务），保持其「仍复核」的断言成立。已在 `progress.md` 记录。

## F6 — 4.4 引导措辞保持中性

- **决策**：`system_prompt.py` 新增「【数据视图目录】」引导行不点名具体工具名（用「describe 类工具 / list 类工具」），避免与 MCP 实际工具名耦合；原有「资源映射」引导（先按字段/口径定位候选、再 describe 确认）已覆盖 R3 意图，新行只补充「任务上下文中的视图目录优先复用」。
