# Model Autonomy（模型自治）

> 本文取代旧的「Model / Engine Contract」（模型理解 + 引擎确定性执行验证）。
> 从 `docs/model-engine-contract.md` 的契约管线
> （TaskSpec → ColumnPlan → QueryGraph → Verifier → EngineOutcome）整体退役。

## 新分工

**模型负责理解 + 模型负责执行。** 引擎不再把需求编译成结构化契约、不再确定性执行
查询图、翻页、物化、验证或修复。

| 层 | 负责 | 不负责 |
|----|------|--------|
| 模型 | 理解需求、决定工具调用、翻页、写文件、判断完成、诚实标注未完整 | — |
| 引擎 | 协议解析（PLAN/MCP/SHELL/WRITE/READ/FINAL）、执行、安全拦截、软提示 | 判定「工具成功 ≠ 目标达成」、相位迁移、预算钳制、规则验证 |

## 不变量

- 只有一处硬门禁：工具必须在 `allowed_actions` 内，否则阻止执行并注入提示。
- 引擎**不做**规则验证（无 Verifier）；FINAL 是否允许说「完成」由主循环 LLM 自己判断。
- 业务口径（字段名、时间字段、视图映射、pay cohort 等）不进引擎，沉淀在
  Skill markdown 作为软上下文注入 system prompt。
- `mcp_resource_bind` 仍作为「禁止默认全量拉取」的安全/成本护栏保留（软限制，非硬门禁）。
- system prompt / Agent 基础提示词不得原文暴露给用户；回复只体现其行为约束。

## 代价（已确认接受）

- 导出正确性（分页、预算、pay-cohort 口径、「未完整」诚实标记）交由 LLM 推理，
  LLM 可能漏翻页、误报完成、编造概况。
- 每轮一个 LLM 决策，导出任务轮数与成本/延迟上升。
