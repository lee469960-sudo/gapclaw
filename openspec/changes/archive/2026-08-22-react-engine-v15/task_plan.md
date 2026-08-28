# Task Plan — react-engine-v15

将 `tasks.md`（唯一正式任务来源）映射为具体执行阶段。本文件**不重新定义需求**，仅把 20 个 task 落到可执行的落地步骤与验收口径；需求与场景以 `specs/` 为准。

## 阶段划分

| 阶段 | 来源 | 任务 | 范围 | 性质 |
|---|---|---|---|---|
| A | agent-runtime R1′ 破局提示 | 1.1–1.4 | `runtime.py` + `loop_state.py` | 新实现（撤销 v14 R1 终止） |
| B | agent-runtime 撤销 R2 | 2.1–2.2 | `runtime.py` | 新实现（删除跳过分支） |
| C | agent-runtime R3 视图目录 | 3.1–3.4 | `utils.py` + `context_manager.py` + `system_prompt.py` | 原样携带（验证仍存在） |
| D | agent-runtime R4 上下文百分比 | 4.1–4.3 | `llm_client.py` / `runtime.py` | 新实现 |
| E | agent-config 绑定 LLM 组 | 5.1–5.2 | `Agents.vue` | 原样携带（验证仍存在） |
| F | 测试与回归 | 6.1–6.5 | `apps/api/tests/` | 新测试 + 全量回归 |

> task 总数 20 = 4+2+4+3+2+5。

## 阶段 A — 撤销 R1 终止，改破局提示（tasks 1.1–1.4）

- **1.1** 删除 `runtime.py` `_soft_budget_check` 的终止动作（`_distill_final` 提前收尾 + `_clear_run_state`）。
  - 落地：移除达阈值时 `_append_step` + `state.final = await self._distill_final(...)` + `_clear_run_state(ctx)` 并 return True 的终止路径。
  - 验收：连续无进展不再提前 `_distill_final` 收尾（对应 spec「不终止循环、不提前收尾」）。
- **1.2** 连续无进展（默认 5 轮）→ 注入模板化「破局复盘」软提示（已完成 + 仍缺 + 二选一），不终止。
  - 落地：达阈值改为注入模板文本：`已完成：<进度>`、`仍缺：<未完成子任务>`、`请二选一：调用工具推进，或输出 FINAL: <当前结论>`。
  - 验收：注入的是模板化文本（非 LLM 生成），且循环继续（对应 spec「提示为软性非门禁」）。
- **1.3** `loop_state.py` 的 `no_progress_streak` 降级为软提示触发计数（非门禁）。
  - 落地：字段语义从「软预算门禁」改为「破局提示触发计数」，更新注释。
  - 验收：字段不再触发终止，仅作提示触发依据。
- **1.4** 触发一次后进入冷却（每 +5 轮再触发），避免刷屏。
  - 落地：记录上次触发轮次，间隔 ≥5 轮才再注入；与既有 `same_sig_run>=3`、`tool_call_tally>=4` 的「每 +N」模式一致。
  - 验收：连续无进展多轮只按间隔触发，不每轮刷屏。

## 阶段 B — 撤销 R2 短任务跳过复核（tasks 2.1–2.2）

- **2.1** 删除 `runtime.py`「短任务（无子任务 + 无交付物）跳过 `_reflect_final`」分支。
  - 落地：移除 FINAL 分支里 `short_task = not subtasks and not saved_paths and not files_written` 及其跳过 `_reflect_final` 的路径。
  - 验收：短任务 FINAL 不再直接接受（对应 spec「所有 FINAL 一律复核」）。
- **2.2** 所有 `FINAL` 一律走 `_reflect_final`；复核拒绝仍按既有逻辑回灌修复清单 / 修订 PLAN 并 replan。
  - 落地：FINAL 处理回到「一律复核」路径；拒绝分支逻辑不变。
  - 验收：无「不复核即接受」路径；拒绝→回灌→replan 语义不变。

## 阶段 C — 数据视图目录缓存与注入（保留 R3，tasks 3.1–3.4）

> 自 v14 原样携带，代码已存在。本阶段为「验证仍存在 + 满足场景」，非新实现（见 findings F2）。

- **3.1** 缓存 MCP `list_ads_views` 视图名清单（跨会话 + TTL）。→ 验证 `utils.py` `_ads_view_cache`（key=mcp_id, TTL 3600）仍在。
- **3.2** 蒸馏出的 `view→字段/口径` 映射持久化复用。→ 验证 `_ads_view_map_cache` 仍在。
- **3.3** 注入 task_context（视图名清单 + 映射），PLAN 阶段可见。→ 验证 `context_manager.set_task_context(view_catalog=...)` 与 `build_ads_view_catalog` 调用仍在。
- **3.4** `system_prompt.py` 视图目录用法引导。→ 验证「【数据视图目录】」引导仍在。

## 阶段 D — 真实会话上下文可用百分比（R4，tasks 4.1–4.3）

- **4.1** 复用 `estimate_tokens` 对「系统提示 + 历史 + 任务上下文 + 工具结果」求和，在 `fit_messages_to_context` 裁剪前计算。
  - 落地：在裁剪入口前，用 `estimate_tokens` 对构造好的消息列表求和得「已用输入 token」。
  - 验收：口径 = 系统提示 + 历史 + 任务上下文 + 工具结果，且在裁剪前（对应 spec「口径一致」）。
- **4.2** 计算可用率 = `(1 − 已用 token / max_context_tokens) × 100%`，夹取 0–100。
  - 落地：`max(0, min(100, round(...)))`。
  - 验收：随占用增长下降、越接近窗口上限越趋向 0%（对应 spec「随占用增长下降」）。
- **4.3** 写入消息 meta 的 `context_available_percent`（前端 `AgentChat.vue` 已读该字段）。
  - 落地：运行结束时写入消息 meta（最小实现，见 findings F6）。
  - 验收：前端能读到真实值，替换硬编码 100%（对应 spec「真实可用率计算」）。

## 阶段 E — Agent 属性面板可绑定 LLM 组（保留，tasks 5.1–5.2）

> 自 v12/v14 原样携带，代码已存在。本阶段为「验证仍存在 + 满足场景」，非新实现（见 findings F2）。

- **5.1** `Agents.vue` 去 `type==='llm'` 过滤，`el-option-group` 分「单模型/模型组」。→ 验证 `singleLlms`/`groupLlms` 与 `el-option-group` 仍在。
- **5.2** `defaultLlmId()` 优先单模型（先 `MinMax`，再首个单模型）。→ 验证默认值计算仍在。

## 阶段 F — 测试与回归（tasks 6.1–6.5）

- **6.1** 破局提示单测：连续无进展 → 注入提示且不终止；有推进 → 清零不注入。
- **6.2** 全程复核单测：短任务 FINAL 仍调 `_reflect_final`；不复核即接受的路径已移除。
- **6.3** 上下文百分比单测：裁剪前口径、随占用下降、复用 `estimate_tokens`。
- **6.4** 视图目录单测：list 结果缓存跨会话；目录注入 task_context。
- **6.5** 全量回归绿；确认无新增循环硬门禁；既有单模型绑定行为不变。

## 执行顺序与依赖

- A、B、D 为新实现，集中在 `runtime.py`（A/B）与 `llm_client.py`/`runtime.py`（D），依赖小，建议按 A→B→D 顺序推进减少冲突。
- C、E 为携带验证，独立，可与 A/B/D 并行或在最后一次性核对。
- F 贯穿各阶段末（对应测试随阶段落地），最后跑 6.5 全量回归。
- 关键前提：v14 已归档（agent-config 与 R3 已同步进主 spec），故 C/E 是「验证已存在」，不是重写。
