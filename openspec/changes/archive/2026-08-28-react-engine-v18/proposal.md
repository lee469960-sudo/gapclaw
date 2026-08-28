## Why

Standard/DBA `_run_modular` 在完成度复核 FAIL 后把 `revised_plan` 写进子任务并显示「已重新规划」，但本轮不跑工具。若模型随后不再输出 FINAL，`reflect_fail_count` 停在 1–2（收敛阈值 3），循环烧光 `max_iters`，任务以「未完成」收尾。同时执行 LLM 兼任裁判时，常吐出空话 `FAIL: 任务尚未完成`（无有效 `fix_list`），把本可结束的候选打回规划空转。

## What Changes

- **[agent-runtime] 子任务证据门**：存在具名子任务时，必须全部 `[x]` 才进入 `_reflect_final`；未完成则丢掉 FINAL、提示执行开口项，不增加失败计数、不进入修复期。
- **[agent-runtime] 空话 FAIL 当 PASS**：`fix_list` 为空或条目全是套话（「任务尚未完成」等）时接受候选，不计入有效 FAIL。
- **[agent-runtime] 有效 FAIL 只回灌修复清单**：不应用 `revised_plan`；置 `fix_only_until_final`，直到下一轮被接受的 FINAL 之前丢弃任何 PLAN。
- **[agent-runtime] 有效 FAIL 计数**：连续 3 次有效 FAIL 仍收敛；真实工具成功（非缓存命中）将计数清零。步骤文案改为「请按修复清单执行」。

## Capabilities

### New Capabilities

<!-- none -->

### Modified Capabilities

- `agent-runtime`: 完成度复核增加证据门与空话 PASS；FAIL 后禁止应用修订 PLAN，只注入可执行修复清单；有效 FAIL 计数在工具成功时清零。

## Impact

- `apps/api/app/services/agent_runtime/runtime.py`（`_run_modular`）
- `apps/api/app/services/agent_runtime/loop_state.py`（`fix_only_until_final` 及 checkpoint）
- `apps/api/tests/test_react_engine_v18.py` 及既有 v5 / PLAN+FINAL 夹具
- 不改：Code Profile Verifier/Sealer、独立裁判模型、第一次 FAIL 即停、循环结束四类事件
