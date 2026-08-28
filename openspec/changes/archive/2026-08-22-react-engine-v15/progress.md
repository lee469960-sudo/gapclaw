# Progress — react-engine-v15

记录实际完成情况。勾选 `tasks.md` 前的落地依据（代码 + 测试 + 回归）。

## 状态总览

- **tasks.md 进度**：20 / 20 ✅
- **阶段**：A/B/C/D/E/F 全部完成
- **回归**：`pytest tests/` → **253 passed, 0 failed**

## 完成记录

### 阶段 A — 撤销 R1 终止，改破局提示（1.1–1.4）

- **1.1** `runtime.py` 删除 `_soft_budget_check`（原无进展软预算提前收尾 + `_clear_run_state`），改为 `_apply_no_progress_hint` 软提示；3 处调用点 `if await _soft_budget_check(...): return _ret(state.final)` → `_apply_no_progress_hint(made_progress, round_no)`。全库已无 `_soft_budget_check` / `_NO_PROGRESS_BUDGET` 残留。
- **1.2** `_apply_no_progress_hint` 在连续 5 轮无进展时注入模板化「破局复盘」提示（已完成 + 仍缺 + 二选一），`cm.add_coach_hint` 软注入，**不终止循环**（max_iters 仍是唯一硬预算）。
- **1.3** `loop_state.py` `no_progress_streak` 保留为软提示触发计数（非门禁），注释标注 v15 R1′。
- **1.4** 冷却由计数器自身得出：`(streak - N) % N == 0`，在 N、2N、3N… 触发，避免刷屏（常量 `_NO_PROGRESS_HINT_EVERY = 5`）。
- **验收测试**：`test_no_progress_injects_breakthrough_hint_and_does_not_stop`（跑满 8 轮不终止 + 注入提示）、`test_progress_resets_streak_and_does_not_inject`（每轮推进清零不注入）。

### 阶段 B — 撤销 R2 短任务跳过复核（2.1–2.2）

- **2.1** `runtime.py` 删除「短任务（无子任务 + 无交付物）跳过 `_reflect_final`」分支；FINAL 一律 `report = await self._reflect_final(ctx, state, candidate)`。
- **2.2** 复核拒绝路径保留：FAIL → 回灌修复清单 / 修订 PLAN → replan（既有 `_apply_plan` + `_REFLECT_FAIL_CONVERGE` 收敛逻辑不变）。
- **验收测试**：`test_short_task_still_reflects`（短任务 FINAL 仍 `reflect_mock.assert_awaited_once()`）。

### 阶段 C — 数据视图目录缓存与注入（保留 R3，3.1–3.4）

v14 已落地，逐项核对仍存在（carryover，未重新实现）：
- **3.1** `utils.py` `_ads_view_cache`（mcp_id → (ts, view names)）+ `record_ads_view_catalog` / `get_ads_views_cached`（跨会话模块级缓存 + TTL）。
- **3.2** `_ads_view_map_cache`（mcp_id → (ts, {view: fields})），`describe_ads_view` 结果蒸馏复用。
- **3.3** `build_ads_view_catalog` → `cm.set_task_context(view_catalog=...)` 注入 task_context（`runtime.py:715/722/724` + `_apply_plan`）。
- **3.4** `system_prompt.py:279-281` 视图目录用法引导（按语义匹配候选、只 describe top 候选）。
- **验收测试**：v14 R3 五条测试保留并通过（`test_react_engine_v14.py`）。

### 阶段 D — 真实会话上下文可用百分比（R4，4.1–4.3）

- **4.1** `llm_client.py` 新增 `estimate_messages_tokens`（复用 `estimate_tokens` + 每条 4 token 开销），`fit_messages_to_context` 改用同一口径；`runtime.py` `_context_available_percent` 对 `cm.messages`（裁剪前、未裁剪上下文层）求和。
- **4.2** `可用率 = (1 − 已用 / max_context_tokens) × 100%`，夹取 0–100，异常/缺省回退 100。
- **4.3** `_ret` 返回 5 元组含 `_context_available_percent`；`run()` → `_save_assistant_message(context_available_percent=...)` → `meta["context_available_percent"]`（`AgentChat.vue` 已读）。
- **验收测试**：`test_context_available_percent_decreases_with_usage`、`test_context_available_percent_clamps_to_zero`、`test_context_available_percent_written_to_meta`。

### 阶段 E — Agent 属性面板可绑定 LLM 组（保留，5.1–5.2）

v14 已落地，逐项核对仍存在（carryover，未重新实现）：
- **5.1** `Agents.vue:557-558` `singleLlms`（`type==='llm'`）/ `groupLlms`（`type==='group'`），`el-option-group` 分「单模型/模型组」（169-174）。
- **5.2** `defaultLlmId()`（643-646）：优先单模型，先 `MinMax`，再首个单模型。

### 阶段 F — 测试与回归（6.1–6.5）

- **6.1** 破局提示单测：`test_react_engine_v15.py`（连续无进展注入且不终止 / 有推进清零不注入）。
- **6.2** 全程复核单测：`test_short_task_still_reflects`。
- **6.3** 上下文百分比单测：三条例（裁剪前口径/随占用下降/复用 estimate_tokens + 写 meta）。
- **6.4** 视图目录单测：`test_react_engine_v14.py` 五条 R3 测试（list 缓存 + 目录注入 task_context）保留并通过。
- **6.5** 全量回归 `pytest tests/` → **253 passed**；无新增循环硬门禁（破局提示为软提示，不 return）；既有单模型绑定行为不变（前端未改动）。
