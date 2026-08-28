## Why

两个线上问题，根因见 `docs/exploration/react-engine-v13.md` 与 grill 结论：

1. **小任务轮次空转**：收敛全靠软 coach hint，无进展没有硬止点（`runtime.py:45-46` 明示「no static stall thresholds」）；`text_only_streak >= 2` 也只是加 hint + `continue`（`runtime.py:1039-1047`）。模型不理会软提示时一路空转到 `max_iters`（`runtime.py:632`），再由 `_distill_final` 出「没完成」总结。叠加**每个 FINAL 都先过 `_reflect_final`**（`runtime.py:942`）+1 轮，FAIL 再 replan +1~2 轮——小任务白交「精准税」。
2. **计划不智能、视图匹配不贴切**：视图→口径知识在 ads-sync-hub Skill references（`utils.py:23-24`），引擎里只有工具目录、**没有数据视图目录**。模型只能 `list_ads_views` → 靠名字猜 → 逐个 `describe`（`_distill_hint` `runtime.py:1849` 正是为治此而生，治标）。

铁律「无静态硬门禁」需为 R1 **松一个口子**（用户已确认）：允许一个「无进展软预算」提前收尾——但收尾是 LLM 生成的诚实总结（复用 `_distill_final`），非粗暴截断。

## What Changes

- **R1 无进展软预算提前收尾**：连续无进展（无工具执行成功、无文件写入、无进度新增、无子任务推进）达到阈值时，提前触发 `_distill_final` 的诚实总结收尾，而非空转到 `max_iters`。
- **R2 小任务跳过完成度复核**：短任务（无多子任务 PLAN、无交付物）的 `FINAL` 直接接受，跳过 `_reflect_final`，省 +1 轮；长任务仍复核。
- **R3 数据视图目录缓存与注入**：缓存 `list_ads_views` 视图名清单（跨会话），连同蒸馏出的 `view→字段/口径` 映射注入 task_context，让 PLAN 一步语义匹配候选视图，describe 只确认 top 候选。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `agent-runtime`: 新增「无进展软预算提前收尾」「小任务跳过完成度复核」「数据视图目录缓存与注入」三条需求。

## Impact

- `apps/api/app/services/agent_runtime/runtime.py`（R1 无进展软预算 + R2 短任务跳过 `_reflect_final` + R3 视图目录注入）
- `apps/api/app/services/agent_runtime/loop_state.py`（R1 无进展计数状态）
- `apps/api/app/services/agent_runtime/system_prompt.py`（R3 视图目录用法引导）
- `apps/api/app/services/agent_runtime/utils.py`（R3 视图目录缓存）
- `apps/api/tests/`（新增单测）
