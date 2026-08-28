# Findings — react-engine-v15（执行中记录）

记录执行过程中发现的问题、张力与决策。与 `progress.md` 互补：本文件记录「问题/决策」，`progress.md` 记录「完成事实」。

## F1 — 任务总数实为 20

- **问题**：tasks.md 分 6 节（1.x–6.x），总数 = 4+2+4+3+2+5 = 20。
- **决策**：以 `tasks.md`（唯一正式任务来源）为准，20 个 task。

## F2 — 阶段 C（R3）与阶段 E（agent-config）是「原样携带」，非新实现

- **问题**：tasks 3.1–3.4（视图目录）与 5.1–5.2（绑定 LLM 组）的代码已由 v14 落地，并随 v14 归档同步进主 spec。v15 对它们「原样携带、无改动」（design D5）。
- **决策**：这两阶段的落地 = 「验证代码仍存在且满足对应 spec 场景」，而非重新实现。勾选前逐一核对代码位置与场景覆盖（铁律：不因「代码已存在」而自动勾选，仍需满足 acceptance criteria）。

## F3 — delta spec 未声明 REMOVED（R1/R2 废弃只在 proposal/design 里）

- **问题**：proposal/design 明确「废弃 v14 R1（无进展软预算提前收尾）与 R2（小任务跳过完成度复核）」，但 `specs/agent-runtime/spec.md` 只有 `## ADDED Requirements`（3 条），没有 `## REMOVED Requirements` 声明这两条被移除。而 v14 归档后，这两条需求已在主 spec `openspec/specs/agent-runtime/spec.md` 里。
- **影响**：到 archive 时，若按 delta 原样 sync，主 spec 里这两条已废弃需求不会被移除（缺 REMOVED 声明），导致「主 spec 残留已废弃行为契约」。
- **已解决**：补 `## REMOVED Requirements`（`无进展软预算提前收尾`、`小任务跳过完成度复核`，各带一句原因）；同时把「数据视图目录缓存与注入」从 `## ADDED` 移除（它是 R3 原样携带，已存在于主 spec，无 delta）。

## F4 — agent-config 在 proposal 标为「New」，但 v14 已创建该 capability

- **问题**：proposal 把 `agent-config` 列入「New Capabilities」，且 delta 用 `## ADDED Requirements`；但 v14 归档已把 `agent-config` 同步进主 spec（`openspec/specs/agent-config/spec.md` 已存在，含同名需求）。
- **影响**：该需求在 delta 里标「ADDED」，但主 spec 已存在同名需求。archive 时按「已存在→更新为一致」处理（幂等，实际无变化）。delta 无 `## Purpose`（对既有 capability 是正确做法）。
- **已解决**：`agent-config` 是「原样携带、无改动」——删除其 delta spec（`specs/agent-config/spec.md`），并把 proposal 的「New Capabilities」段移除（无新增 capability，仅 `agent-runtime` 为 Modified）。R3（数据视图目录）同理从 agent-runtime 的 ADDED 中移除。

## F5 — 破局提示的阈值/冷却为开放问题（design Open Question）

- **问题**：design 将「连续 5 轮」与冷却间隔的具体取值列为开放问题。
- **决策**：采用默认值——触发阈值 5（与 v14 `_NO_PROGRESS_BUDGET = 5` 一致），冷却间隔 5 轮（每 +5 轮再触发），与既有「每 +N」模式一致。实现后在此标注实际取值。

## F6 — R4 百分比是否实时推送为开放问题（design Open Question）

- **问题**：design 将「R4 百分比是否运行中实时推送（hub/websocket 步进）vs 仅结束时写入消息 meta」列为开放问题。
- **决策**：采用最小实现「结束时写入消息 meta」；前端 `AgentChat.vue` 读该字段即生效。若后续需实时步进再扩展。实现后在此标注。
