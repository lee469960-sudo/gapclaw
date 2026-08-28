# Progress — react-engine-v14

记录实际完成情况。勾选 `tasks.md` 前的落地依据（代码 + 测试 + 回归）。

## 状态总览

- **tasks.md 进度**：17 / 18（仅 5.1 手动前端验证待用户确认）
- **阶段**：A/B/C/D 全部落地；E 中 5.2–5.5 完成，5.1 待手动
- **回归**：`pytest tests/` 全绿（251 passed）

## 完成记录

### 阶段 A — agent-config 可绑定 LLM 组（V12，前端）

- **1.1** ✅ 落地：`apps/web/src/views/Agents.vue:607` 改为 `llms.value = data.llms || []`（去掉 `filter(l => l.type === 'llm')`）。验收：`llms` 同时含 `llm` 与 `group`。
- **1.2** ✅ 落地：`Agents.vue:557-558` 新增 `singleLlms`/`groupLlms` 计算属性。验收：两组按 `l.type` 互斥拆分、无遗漏。
- **1.3** ✅ 落地：`Agents.vue:169-174` 用 `el-option-group`（单模型/模型组）分组渲染。验收：分组视觉区分。
- **1.4** ✅ 落地：`Agents.vue:643-646` `defaultLlmId()` 改为 `singleLlms.value` 中先 `MinMax`、再首个单模型。验收：默认仍是单模型（与旧 `llms.value` 全为 `type:llm` 时行为等价）。

> 前端无自动化测试；1.1–1.4 通过代码走查确认，最终视觉验收由 **5.1（手动）** 兜底。

### 阶段 B — 无进展软预算提前收尾（V13 R1）

- **2.1** ✅ 落地：`loop_state.py:30` 新增 `no_progress_streak: int = 0`。
- **2.2** ✅ 落地：`runtime.py` 每轮 `made_progress` 重置；推进信号宽口径覆盖 spec 四类——工具成功（非缓存命中）、文件写入（file_write 成功即工具成功）、进度新增（成功路径内）、子任务推进（`_apply_plan` 后 done 数增加）。缓存命中不算推进（防同命令重跑被误判为有进展）。
- **2.3** ✅ 落地：`runtime.py` 新增 `_NO_PROGRESS_BUDGET = 5` + 内层 `_soft_budget_check`；达阈值复用 `_distill_final`（LLM 诚实总结、标注缺口）+ `_clear_run_state` 收尾，不空转到 `max_iters`。应用点：纯文本轮、复核拒绝轮、工具执行轮末尾；LLM 传输/参数错误轮不计数（已由既有 3/2 阈值处理）。

### 阶段 C — 小任务跳过完成度复核（V13 R2）

- **3.1** ✅ 落地：`runtime.py` FINAL 分支先判 `short_task = not subtasks and not saved_paths and not files_written`，命中则跳过 `_reflect_final`、直接接受 FINAL。
- **3.2** ✅ 落地：判定不命中时保持原 `_reflect_final` 路径不变（有子任务/交付物仍复核）。

### 阶段 D — 数据视图目录缓存与注入（V13 R3）

- **4.1** ✅ 落地：`utils.py` 新增 MCP 级 `list_ads_views` 视图名清单缓存（`_ads_view_cache`，key=mcp_id，TTL 3600s）；`agent_tools.py` 在 MCP 分发成功后 `record_ads_view_catalog` 落缓存。见 [[findings F2]]。
- **4.2** ✅ 落地：`utils.py` 新增 `_ads_view_map_cache`（`{view: 字段结构摘要}`），describe 类工具结果经 `_json_keys_summary` 蒸馏后缓存复用。见 [[findings F3]]。
- **4.3** ✅ 落地：`context_manager.py::set_task_context` 增加 `view_catalog` 参数渲染「【数据视图目录】」；`runtime.py` 初始 task_context 与 `_apply_plan` 均注入 `build_ads_view_catalog(ctx.mcp_ids)`。
- **4.4** ✅ 落地：`system_prompt.py` 新增「【数据视图目录】」引导（语义匹配候选 → describe 只确认 top 候选，不逐个 describe）。措辞中性，不点名工具。见 [[findings F6]]。

### 阶段 E — 测试与回归

- **5.1** ⏳ **待手动**：需在浏览器验证下拉框分组、组绑定保存、列表 `llm_name` 显示组名、默认仍单模型。代码已就绪（阶段 A），但视觉验收无法在 CLI 内完成。
- **5.2** ✅ 落地：`tests/test_react_engine_v14.py` `test_no_progress_budget_distills_and_stops_early`（连续无进展 5 轮提前收尾、`chat_completion` 仅 5 次）+ `test_progress_resets_streak_and_does_not_trigger`（有推进不触发）。
- **5.3** ✅ 落地：`test_short_task_skips_reflect`（短任务不调 `_reflect_final`）+ `test_long_task_still_reflects`（有子任务仍调）。
- **5.4** ✅ 落地：`test_record_and_build_ads_view_catalog` / `test_ads_view_catalog_empty_when_no_cache` / `test_ads_view_catalog_error_result_not_cached` / `test_task_context_injects_view_catalog` / `test_task_context_omits_view_catalog_when_absent`。
- **5.5** ✅ 落地：`pytest tests/` 全绿 251 passed。仅 R1 新增 `_NO_PROGRESS_BUDGET` 阈值，无其它循环门禁/静态阈值；`defaultLlmId` 语义与旧单模型行为等价。R2 语义变化引发 2 个 legacy 测试改为长任务（见 [[findings F5]]），非回归。
