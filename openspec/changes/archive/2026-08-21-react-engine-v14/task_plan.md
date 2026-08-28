# Task Plan — react-engine-v14

将 `tasks.md`（唯一正式任务来源）映射为具体执行阶段。本文件**不重新定义需求**，仅把 18 个 task 落到可执行的落地步骤与验收口径；需求与场景以 `specs/` 为准。

## 阶段划分

| 阶段 | 来源 | 任务 | 范围 |
|---|---|---|---|
| A | agent-config (V12) | 1.1–1.4 | 前端 `Agents.vue` 模型下拉框 |
| B | agent-runtime R1 (V13) | 2.1–2.3 | `loop_state.py` + `runtime.py` 无进展软预算 |
| C | agent-runtime R2 (V13) | 3.1–3.2 | `runtime.py` 短任务跳过复核 |
| D | agent-runtime R3 (V13) | 4.1–4.4 | `utils.py` + `runtime.py` + `system_prompt.py` 视图目录 |
| E | 测试与回归 | 5.1–5.5 | `apps/api/tests/` + 手动验证 |

## 阶段 A — agent-config 可绑定 LLM 组（V12，纯前端）

- **1.1** 去掉 `Agents.vue` 中 `llms.value = (...).filter((l) => l.type === 'llm')` 的类型过滤。
  - 落地：删除/移除该 `filter` 谓词，使 `llms` 同时含 `type:"llm"` 与 `type:"group"`。
  - 验收：下拉框不再过滤掉 `group`（对应 spec「下拉框包含组」）。
- **1.2** 新增 `singleLlms` / `groupLlms` 计算属性，按 `l.type` 拆分。
  - 落地：`computed(() => llms.value.filter(l => l.type === 'llm'))` 与 `l.type === 'group'`。
  - 验收：两组互斥、无遗漏。
- **1.3** 模型下拉框用 `el-option-group` 分「单模型」/「模型组」两组渲染。
  - 落地：`<el-option-group label="单模型">…</el-option-group>` 与「模型组」各渲染各自 `el-option`。
  - 验收：分组视觉区分（对应 spec「分组视觉区分」）。
- **1.4** `defaultLlmId()` 优先单模型（先 `MinMax`、再首个 `singleLlms` 项，空则退回）。
  - 落地：默认值计算改为优先 `MinMax` 单模型，否则首个单模型，不默认选组。
  - 验收：新建 Agent 未选模型时默认单模型（对应 spec「默认优先单模型」）。

## 阶段 B — 无进展软预算提前收尾（V13 R1）

- **2.1** `loop_state.py` 加 `no_progress_streak` 状态字段。
  - 落地：在 loop state 结构上新增字段（默认 0），随 state 读写/持久化保持一致。
  - 验收：字段存在且可跨轮保持。
- **2.2** `runtime.py` 每轮末判定「有无推进」（工具成功/写文件/进度新增/子任务推进），无推进 +1、有推进清零。
  - 落地：在主循环每轮末汇总推进信号，推进→清零，无推进→`+1`。
  - 验收：推进信号宽口径覆盖 spec「有推进不触发」所列四类。
- **2.3** 达到阈值（默认 5）→ 复用 `_distill_final` 诚实总结收尾 + `_clear_run_state`，不空转到 `max_iters`。
  - 落地：`no_progress_streak >= 5` 时触发 `_distill_final` 生成诚实总结并 `_clear_run_state` 收尾。
  - 验收：收尾由 LLM 生成、标注已完成与缺口，非粗暴截断（对应 spec「持续无进展触发软收尾」「收尾为 LLM 诚实总结」）。

## 阶段 C — 小任务跳过完成度复核（V13 R2）

- **3.1** FINAL 分支：`state.subtasks` 空 且 `saved_paths`/`files_written` 空 → 跳过 `_reflect_final` 直接收尾。
  - 落地：FINAL 处理处加短任务判定，命中则绕过 `_reflect_final`。
  - 验收：无子任务/交付物时不调 `_reflect_final`（对应 spec「短任务直接收尾」）。
- **3.2** 有子任务或交付物 → 仍走 `_reflect_final`。
  - 落地：判定不命中时保持原有复核路径不变。
  - 验收：长任务仍复核（对应 spec「长任务仍复核」）。

## 阶段 D — 数据视图目录缓存与注入（V13 R3）

- **4.1** `utils.py` 缓存 MCP `list_ads_views` 视图名清单（跨会话 + TTL）。
  - 落地：新增跨会话缓存（TTL + 失效策略），复用既有 tools/list TTL 缓存机制，不另起并行缓存。
  - 验收：首次列举后缓存，跨会话复用（对应 spec「视图名清单缓存」）。
- **4.2** 蒸馏出的 `view→字段/口径` 映射持久化复用。
  - 落地：视图名清单对应蒸馏映射一并缓存/持久化。
  - 验收：映射可跨会话复用。
- **4.3** 注入 task_context（视图名清单 + 映射），PLAN 阶段可见。
  - 落地：构建 task_context 时注入清单与映射。
  - 验收：PLAN 阶段模型可见（对应 spec「注入 task_context」「一步语义匹配」）。
- **4.4** `system_prompt.py` 视图目录用法引导（按语义匹配候选、describe 只确认 top 候选）。
  - 落地：系统提示新增视图目录用法引导，中性措辞、不点名具体工具。
  - 验收：引导「语义匹配候选 → describe 只确认 top 候选」，不逐个 describe 全部视图。

## 阶段 E — 测试与回归

- **5.1** 手动验证：下拉框同时出现单模型与模型组、分组可区分；绑定组保存成功、列表 `llm_name` 显示组名；默认仍单模型。
- **5.2** 无进展软预算单测（连续无进展 → 提前收尾；有推进 → 不触发；收尾为 LLM 诚实总结）。
- **5.3** 短任务跳过复核单测（无子任务/交付物 → 不调 `_reflect_final`；有交付物 → 仍调）。
- **5.4** 视图目录单测（list 结果缓存跨会话；目录注入 task_context）。
- **5.5** 全量回归绿；除 R1 外无新增循环门禁 / 静态阈值；既有单模型绑定行为不变。

## 执行顺序与依赖

A（前端独立）→ 可先或并行；B/C/D 均在 `runtime.py` 不同区域，依赖小，按 B→C→D 顺序推进以减少冲突；E 贯穿各阶段末（对应测试随阶段落地），最后跑 5.5 全量回归。
