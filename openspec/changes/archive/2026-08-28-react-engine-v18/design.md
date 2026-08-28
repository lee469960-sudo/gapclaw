## Context

Standard/DBA 完成度复核（`_reflect_final`）与执行模型是同一个 Agent LLM。FAIL 后原先 `_apply_plan(revised_plan)` 再 `continue`，本轮不执行工具；空话 FAIL 也会推高空转。Code Profile 有独立 Verifier/Sealer，不在本 change。

铁律不变：循环只由 FINAL / 取消 / LLM 错误 / `max_iters` 结束。证据门与修复期都是软路径（丢 FINAL / 丢 PLAN + coach），不是新硬门。

## Goals / Non-Goals

**Goals:**

- 未完成子任务不得进入复核，避免「完成度复核未通过，已重新规划」空转至预算耗尽。
- 无有效 `fix_list` 的 FAIL 不得阻断收尾。
- 有效 FAIL 之后只执行修复清单，禁止再应用任何 PLAN，直到下一次被接受的 FINAL。
- 连续 3 次有效 FAIL 仍收敛；真实工具成功清零计数。

**Non-Goals:**

- 独立裁判模型、第一次 FAIL 即停、Code Profile。
- 把 `reflect_fail_count` 写入 checkpoint（保持进程内计数，与既有行为一致）。

## Decisions

### D1: 证据门在 `_reflect_final` 之前，按子任务清单

有具名子任务则须全部 `status == "done"`。无子任务的短任务仍走复核（与 v15 全程复核一致）。证据门拒绝不置 `fix_only_until_final`、不增加 `reflect_fail_count`。同轮非 FINAL 工具在丢掉 FINAL 后仍执行。

### D2: 有效 FAIL 的唯一证据是非空、非套话的 `fix_list`

套话清单与共识锁定文案一致。`missing` 单独再具体也不算有效 FAIL（空 `fix_list` → PASS）。复核器仍可输出 `revised_plan`，循环 MUST NOT 应用。

### D3: `fix_only_until_final` 写入 checkpoint

进程崩溃后续跑仍须挡住 PLAN。`reflect_fail_count` 不持久化：续跑从 0 再计有效 FAIL（与改前一致）。

### D4: 计数清零只认真实工具成功

`made_progress` 里非缓存命中的成功执行将 `reflect_fail_count` 置 0。PLAN 勾选推进、缓存命中不重置。

## Risks / Trade-offs

- PLAN+FINAL 同轮且 PLAN 含 `[ ]` 时不再当轮收尾——这是证据门的预期，旧夹具改为 `[x]` + FINAL。
- 空话 PASS 可能放过真未完成的短任务；用有效 `fix_list` 换可执行修复，优先堵住空转。
