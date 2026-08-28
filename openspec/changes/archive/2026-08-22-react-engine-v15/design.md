# Design — react-engine-v15

## Context

v15 是 v14 的修正版，自包含、取代 v12/v13/v14。核心原则——**以完成任务为第一目标**——驱动了本轮对 v14 R1/R2 的撤销。

**v14 的三个问题**（根因见 `docs/exploration/react-engine-v15.md`）：
- R2「短任务跳过复核」用「无交付物」这个错误信号跳过了「答案正确性」校验，坏答案直接露出。
- R1「无进展软预算」是「放弃」机制，连续 5 轮无进展即终止，掐断本可能完成的任务。
- 会话上下文百分比是硬编码 100% 回退，不真实。

## Goals / Non-Goals

**Goals:**
- 撤销 R1 的「放弃收尾」，改「破局提示」，让模型在卡壳时被点醒而非被放弃。
- 撤销 R2 的「跳过复核」，所有 `FINAL` 都过 `_reflect_final`，坏答案不露出。
- 上下文百分比按真实 token 计算，用户能看到「离窗口满还差多少」。
- 保留 v14 中仍有效的 agent-config（绑定 LLM 组）与 R3（视图目录）。

**Non-Goals:**
- 不新增任何「仍在推进就截断」的硬停。
- 不改 MCP / Skill 内容。
- 不改 group failover / 环检测 / 退避重试（v10/v11 已定）。
- 不触及 Executor / Checkpoint-Recovery / Retry / Concurrency / Benchmark 各维。

## Decisions

| # | 决策 | 内容 |
|---|---|---|
| D1 | 撤销 R1 终止 | 删除 `_soft_budget_check` 的终止动作；回到 `max_iters` 唯一硬预算 |
| D2 | 无进展 → 破局提示 | 连续 5 轮无进展时注入模板化「破局复盘」提示（已完成 + 仍缺 + 二选一），软性、不终止 |
| D3 | 撤销 R2 跳过 | 删除「短任务跳过 `_reflect_final`」分支，所有 `FINAL` 一律复核 |
| D4 | 上下文真实口径 | 可用率 = `(1 − 已用输入 token / max_context_tokens) × 100%`，裁剪前算 |
| D5 | 保留 agent-config + R3 | 自 v14 原样携带，无改动 |
| D6 | 交付形态 | 新建 openspec v15，自包含，取代 v12/v13/v14 |

## Rejected Alternatives（废弃设计，不进入 v15）

| 废弃项 | 来源 | 废弃原因 |
|---|---|---|
| 无进展软预算提前收尾（R1） | v14 | 「放弃」机制，掐断本可完成的任务，违背「完成任务优先」；被 D1/D2 的「破局提示」取代 |
| 小任务跳过完成度复核（R2） | v14 | 用「无交付物」误判「无需校验」，让放弃/错误答案直接露出；被 D3「全程复核」取代 |

## 与 v14 的关系

v15 自包含，取代 v12/v13/v14：agent-config（来自 v12）与 R3（来自 v13）原样携带；R1/R2 明确废弃不进入 v15。落地后 v12/v13/v14 应归档、不 apply（见 Open Questions）。

## Risks / Trade-offs

- [风险] 撤销 R1 后，小任务空转最多到 `max_iters`（默认 50）才由 `_distill_final` 收尾。→ 缓解：`max_iters` 可经 Agent `max_iterations` 配置；D2 的破局提示在 5 轮即介入，降低空转概率。
- [风险] 撤销 R2 后，每个 FINAL 多花一轮复核。→ 接受：完成优先于 +1 轮效率；这是正确性兜底。
- [风险] R4 的 `estimate_tokens` 是 ≈2 字符/token 的粗略估算，符号密集代码会被低估。→ 缓解：仅作百分比展示，不参与裁剪决策（裁剪仍用 `fit_messages_to_context` 的自带余量）；文档标注「估算」。
- [风险] 破局提示若连续触发会刷屏。→ 缓解：触发一次后进入冷却（如每 +5 轮再触发一次），与既有 `same_sig_run>=3`、`tool_call_tally>=4` 的「每 +N」模式一致。

## Open Questions

- 破局提示触发阈值「连续 5 轮」与冷却间隔的具体取值。
- v12/v13/v14 的归档动作（本次仅新建 v15，归档待指令）。
- R4 的百分比是否需要在运行中「实时」推送（hub/websocket 步进），还是仅结束时写入消息 meta（最小实现：结束时写入）。
