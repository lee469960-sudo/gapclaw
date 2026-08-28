## 1. R1 — 无进展软预算提前收尾（agent-runtime）

- [ ] 1.1 `loop_state.py` 加 `no_progress_streak` 状态字段
- [ ] 1.2 `runtime.py` 每轮末判定「有无推进」（工具成功/写文件/进度新增/子任务推进），无推进 +1、有推进清零
- [ ] 1.3 达到阈值（默认 5）→ 复用 `_distill_final` 诚实总结收尾 + `_clear_run_state`，不空转到 `max_iters`

## 2. R2 — 小任务跳过完成度复核（agent-runtime）

- [ ] 2.1 FINAL 分支：`state.subtasks` 空 且 `saved_paths`/`files_written` 空 → 跳过 `_reflect_final` 直接收尾
- [ ] 2.2 有子任务或交付物 → 仍走 `_reflect_final`

## 3. R3 — 数据视图目录缓存与注入（agent-runtime）

- [ ] 3.1 `utils.py` 缓存 MCP `list_ads_views` 视图名清单（跨会话 + TTL）
- [ ] 3.2 蒸馏出的 `view→字段/口径` 映射持久化复用
- [ ] 3.3 注入 task_context（视图名清单 + 映射），PLAN 阶段可见
- [ ] 3.4 `system_prompt.py` 视图目录用法引导（按语义匹配候选、describe 只确认 top 候选）

## 4. 测试与回归

- [ ] 4.1 无进展软预算单测（连续无进展 → 提前收尾；有推进 → 不触发；收尾为 LLM 诚实总结）
- [ ] 4.2 短任务跳过复核单测（无子任务/交付物 → 不调 `_reflect_final`；有交付物 → 仍调）
- [ ] 4.3 视图目录单测（list 结果缓存跨会话；目录注入 task_context）
- [ ] 4.4 全量回归绿；除 R1 外无新增循环门禁 / 静态阈值
