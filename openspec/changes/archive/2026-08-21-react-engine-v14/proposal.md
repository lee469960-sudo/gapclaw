## Why

合并 react-engine-v12 与 react-engine-v13，形成一套统一规范。两者**正交互补**，合并后是一份覆盖「Agent 配置面 + 运行时面」的完整变更，而非两个孤立 change：

- **V12（配置面 / 前端）**：Agent 属性面板无法绑定 LLM 组。`apps/web/src/views/Agents.vue:598` 的 `filter((l) => l.type === 'llm')` 把 `type:"group"` 的 LLM 全滤掉，模型下拉框只显示单模型；后端 `refs`（`agent.py:122-126`）、`_validate_refs`（`agent.py:151`）、运行时 group failover（v10）+ 环检测（v11）均已就绪，唯独 UI 没放行。纯前端修复，后端零改动。
- **V13（运行时面 / 后端）**：两个线上问题——① 小任务轮次空转：收敛全靠软 coach hint，无进展没有硬止点（`runtime.py:45-46` 明示「no static stall thresholds」），叠加每个 FINAL 都先过 `_reflect_final`（`runtime.py:942`）+1 轮，小任务白交「精准税」；② 计划不智能、视图匹配不贴切：视图→口径知识在 ads-sync-hub Skill references（`utils.py:23-24`），引擎里只有工具目录、**没有数据视图目录**，模型只能 `list_ads_views` → 靠名字猜 → 逐个 `describe`。

铁律「无静态硬门禁」为 V13 的「无进展软预算」**松一个口子**（已确认）：允许一个无进展软预算提前收尾——收尾是 LLM 生成的诚实总结（复用 `_distill_final`），非粗暴截断。

## What Changes

- **[agent-config] Agent 属性面板可绑定 LLM 组**（V12）：模型下拉框去 `type === 'llm'` 过滤，`el-option-group` 分「单模型/模型组」；`defaultLlmId` 保持优先单模型。
- **[agent-runtime] 无进展软预算提前收尾**（V13 R1）：连续无进展达到阈值时提前触发 `_distill_final` 诚实总结收尾，而非空转到 `max_iters`。
- **[agent-runtime] 小任务跳过完成度复核**（V13 R2）：短任务（无多子任务 PLAN、无交付物）的 `FINAL` 直接接受，跳过 `_reflect_final`，省 +1 轮。
- **[agent-runtime] 数据视图目录缓存与注入**（V13 R3）：缓存 `list_ads_views` 视图名清单（跨会话）+ 蒸馏出的 `view→字段/口径` 映射，注入 task_context，让 PLAN 一步语义匹配候选视图。

## Capabilities

### New Capabilities

- `agent-config`: Agent 配置面契约——属性面板的模型绑定（含 LLM 组）、配置项展示与保存。

### Modified Capabilities

- `agent-runtime`: 新增「无进展软预算提前收尾」「小任务跳过完成度复核」「数据视图目录缓存与注入」三条需求。

## Impact

- `apps/web/src/views/Agents.vue`（V12：模型下拉框去过滤 + `el-option-group` 分组 + `defaultLlmId` 优先单模型）
- `apps/api/app/services/agent_runtime/runtime.py`（V13 R1 无进展软预算 + R2 短任务跳过 `_reflect_final` + R3 视图目录注入）
- `apps/api/app/services/agent_runtime/loop_state.py`（V13 R1 无进展计数状态）
- `apps/api/app/services/agent_runtime/system_prompt.py`（V13 R3 视图目录用法引导）
- `apps/api/app/services/agent_runtime/utils.py`（V13 R3 视图目录缓存）
- `apps/api/tests/`（新增单测）
