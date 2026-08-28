## 1. agent-runtime — 撤销 R1 终止，改破局提示

- [x] 1.1 `runtime.py` 删除 `_soft_budget_check` 的终止动作（`_distill_final` 提前收尾 + `_clear_run_state`）
- [x] 1.2 `runtime.py` 将连续无进展（默认 5 轮）触发改为注入模板化「破局复盘」软提示（已完成 + 仍缺 + 二选一），不终止循环
- [x] 1.3 `loop_state.py` 的 `no_progress_streak` 降级为软提示触发计数（非门禁）
- [x] 1.4 触发一次后进入冷却（每 +5 轮再触发），避免刷屏

## 2. agent-runtime — 撤销 R2 短任务跳过复核

- [x] 2.1 `runtime.py` 删除「短任务（无子任务 + 无交付物）跳过 `_reflect_final`」分支
- [x] 2.2 所有 `FINAL` 一律走 `_reflect_final`；复核拒绝仍按既有逻辑回灌修复清单 / 修订 PLAN 并 replan

## 3. agent-runtime — 数据视图目录缓存与注入（保留 R3）

- [x] 3.1 缓存 MCP `list_ads_views` 视图名清单（跨会话 + TTL）
- [x] 3.2 蒸馏出的 `view→字段/口径` 映射持久化复用
- [x] 3.3 注入 task_context（视图名清单 + 映射），PLAN 阶段可见
- [x] 3.4 `system_prompt.py` 视图目录用法引导（按语义匹配候选、describe 只确认 top 候选）

## 4. agent-runtime — 真实会话上下文可用百分比（R4）

- [x] 4.1 复用 `estimate_tokens` 对「系统提示 + 历史 + 任务上下文 + 工具结果」求和，在 `fit_messages_to_context` 裁剪前计算
- [x] 4.2 计算可用率 = `(1 − 已用 token / max_context_tokens) × 100%`，夹取 0–100
- [x] 4.3 写入消息 meta 的 `context_available_percent`（前端 `AgentChat.vue` 已读该字段）

## 5. agent-config — Agent 属性面板可绑定 LLM 组（保留）

- [x] 5.1 `Agents.vue` 去 `type==='llm'` 过滤，`el-option-group` 分「单模型/模型组」
- [x] 5.2 `defaultLlmId()` 优先单模型（先 `MinMax`，再首个单模型）

## 6. 测试与回归

- [x] 6.1 破局提示单测（连续无进展 → 注入提示且不终止；有推进 → 清零不注入）
- [x] 6.2 全程复核单测（短任务 FINAL 仍调 `_reflect_final`，不复核即接受的路径已移除）
- [x] 6.3 上下文百分比单测（裁剪前口径、随占用下降、复用 `estimate_tokens`）
- [x] 6.4 视图目录单测（list 结果缓存跨会话；目录注入 task_context）
- [x] 6.5 全量回归绿；确认无新增循环硬门禁；既有单模型绑定行为不变
