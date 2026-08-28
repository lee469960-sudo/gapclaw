# Findings — react-engine-v2

> 执行过程中发现的问题、偏差、需决策的点记录于此。**发现一条记录一条**，不攒到收尾才补。
> 只记录「执行中新发现」，不重复 `proposal.md`/`design.md` 已记录的既定决策与风险。

## 记录格式（每条一条）

```markdown
## F<n> — <一句话标题>
- 阶段: P1–P3
- 关联任务: tasks.md <x.y>
- 严重度: blocker / major / minor / note
- 描述: <问题/偏差/需决策>
- 影响: <对 scope / 验收 / 后续任务的影响>
- 状态: open / resolved / wontfix
- 结论/处置: <若 resolved/wontfix 填原因与去向;open 留待决策>
```

---

<!-- 尚无发现。执行时按上述格式追加。 -->

## F1 — spec 场景「PLAN 附带裸代码被救援」不可达，已移除该场景
- 阶段: P3
- 关联任务: tasks.md 3.3
- 严重度: minor
- 描述: 原 spec Scenario「PLAN 附带裸代码被救援时不发提示」假设「PLAN + 裸代码」同轮会被 `_rescue_leaked_code` 救援执行。实测 `_rescue_leaked_code` 要求整段（去 reasoning/tool-token 后）所有行都匹配 shell 命令（`_SHELL_CMD_RE`），或整段以 Python 起始；而 `PLAN:` 行不匹配 shell 正则，故含 PLAN 文本的回复**永远不会**触发救援。救援只对「纯裸代码、无协议行」的回复生效，此时 `plan_steps` 必然为空、`if plan_steps:` 恒 False，提示本就不会发。故「PLAN+救援」是互斥的不可达组合。
- 影响: design D2「提示放 `else:` 而非顶部」的原始动机（避免与救援执行矛盾）实际为防御性冗余——两个落点观感行为一致。落点保留 `else:`（语义上即「确无可执行内容」，成本为零）。spec 该场景与需求正文的「（含被救援的裸代码）」括号因此是误导性描述，一并移除；需求语义不变（有工具执行或 FINAL 就不发提示）。
- 状态: resolved
- 结论/处置: 移除 spec 该场景 + 需求正文括号，tasks 3.3 收窄为「PLAN+FINAL 同轮不发」；落点仍按 `else:` 实现。行为契约未变化，仅删除不可达场景。
