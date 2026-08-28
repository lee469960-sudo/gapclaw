## Why

v14 上线后暴露三个问题（根因见 `docs/exploration/react-engine-v15.md`），都指向同一件事：v14 的 R1/R2 是「允许模型放弃」的机制，与**以完成任务为第一目标**正面冲突。

1. **短任务跳过复核 → TG 显示「任务失败」**：`runtime.py` 的 R2 跳过条件是「无子任务 + 无交付物」，但 `_reflect_final` 校验的是**答案是否满足目标实质要求**，不是只看有没有交付文件。问答型短任务照样会输出放弃/错误的答案，跳过复核后坏答案直接露出（「任务失败」是模型自己的 FINAL 文本，非代码字符串）。
2. **连续无进展 → 诚实总结收尾**：`made_progress` 只有「子任务推进」「工具执行成功（非缓存命中、非失败）」两处置 True，纯文本 / PLAN-only / 工具失败 / 缓存命中 / replan 轮全都不算进展；连续 5 轮即调 `_distill_final` **放弃**收尾。这是「放弃」机制，不是「破局」机制。
3. **会话上下文百分比不真实**：后端从不写 `context_available_percent`，前端读不到就硬编码回退 100%。但后端已有可复用的真实口径（`estimate_tokens` + `llm.max_context_tokens`）。

本轮修正：**撤销 R1/R2 的「放弃/跳过」**，改为「破局提示 + 全程复核」，并补上真实上下文百分比。

## What Changes

- **[agent-runtime] 无进展破局复盘提示（替换 R1）**：连续 5 轮无进展时，注入模板化「破局复盘」软提示（已完成 + 仍缺 + 二选一），**不终止循环**。
- **[agent-runtime] 撤销短任务跳过复核（废弃 R2）**：所有 `FINAL` 一律过 `_reflect_final`。
- **[agent-runtime] 数据视图目录缓存与注入（保留 R3）**：自 v14 原样携带，无改动（已存在于主 spec，无 delta）。
- **[agent-runtime] 真实会话上下文可用百分比（新增 R4）**：按真实 token 计算并写入 `context_available_percent`，替换硬编码 100% 回退。
- **[agent-config] Agent 属性面板可绑定 LLM 组（保留）**：自 v14 原样携带，无改动（已存在于主 spec，无 delta）。

## Capabilities

### Modified Capabilities

- `agent-runtime`: 新增「无进展破局复盘提示」「真实会话上下文可用百分比」；删除「无进展软预算提前收尾」「小任务跳过完成度复核」。「数据视图目录缓存与注入」自 v14 原样携带、无 delta。

## Impact

- `apps/api/app/services/agent_runtime/runtime.py`（删除 R1 终止 + R2 跳过；新增 R1′ 破局提示；新增 R4 上下文百分比计算）
- `apps/api/app/services/agent_runtime/loop_state.py`（`no_progress_streak` 降级为软提示触发计数）
- `apps/api/app/services/llm_client.py` / `agent_runtime/context_manager.py`（复用 `estimate_tokens` 求和，产出可用率）
- `apps/web/src/views/AgentChat.vue`（确认读取真实 `context_available_percent`）
- `apps/web/src/views/Agents.vue`（agent-config：绑定 LLM 组，携带自 v12）
- `apps/api/tests/`（新增单测）
