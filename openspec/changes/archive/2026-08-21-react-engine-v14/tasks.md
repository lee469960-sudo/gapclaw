## 1. agent-config — Agent 属性面板可绑定 LLM 组（V12）

- [x] 1.1 `Agents.vue` 去掉 `llms.value = (...).filter((l) => l.type === 'llm')` 的类型过滤
- [x] 1.2 新增 `singleLlms` / `groupLlms` 计算属性，按 `l.type` 拆分
- [x] 1.3 模型下拉框用 `el-option-group` 分「单模型」/「模型组」两组渲染
- [x] 1.4 `defaultLlmId()` 优先单模型（先 `MinMax`、再首个 `singleLlms` 项，空则退回）

## 2. agent-runtime — 无进展软预算提前收尾（V13 R1）

- [x] 2.1 `loop_state.py` 加 `no_progress_streak` 状态字段
- [x] 2.2 `runtime.py` 每轮末判定「有无推进」（工具成功/写文件/进度新增/子任务推进），无推进 +1、有推进清零
- [x] 2.3 达到阈值（默认 5）→ 复用 `_distill_final` 诚实总结收尾 + `_clear_run_state`，不空转到 `max_iters`

## 3. agent-runtime — 小任务跳过完成度复核（V13 R2）

- [x] 3.1 FINAL 分支：`state.subtasks` 空 且 `saved_paths`/`files_written` 空 → 跳过 `_reflect_final` 直接收尾
- [x] 3.2 有子任务或交付物 → 仍走 `_reflect_final`

## 4. agent-runtime — 数据视图目录缓存与注入（V13 R3）

- [x] 4.1 `utils.py` 缓存 MCP `list_ads_views` 视图名清单（跨会话 + TTL）
- [x] 4.2 蒸馏出的 `view→字段/口径` 映射持久化复用
- [x] 4.3 注入 task_context（视图名清单 + 映射），PLAN 阶段可见
- [x] 4.4 `system_prompt.py` 视图目录用法引导（按语义匹配候选、describe 只确认 top 候选）

## 5. 测试与回归

- [ ] 5.1 手动验证：新建/编辑 Agent 时模型下拉框同时出现单模型与模型组、分组可区分；绑定组保存成功、列表 `llm_name` 显示组名；默认模型仍为单模型
- [x] 5.2 无进展软预算单测（连续无进展 → 提前收尾；有推进 → 不触发；收尾为 LLM 诚实总结）
- [x] 5.3 短任务跳过复核单测（无子任务/交付物 → 不调 `_reflect_final`；有交付物 → 仍调）
- [x] 5.4 视图目录单测（list 结果缓存跨会话；目录注入 task_context）
- [x] 5.5 全量回归绿；除 R1 外无新增循环门禁 / 静态阈值；既有单模型绑定行为不变
