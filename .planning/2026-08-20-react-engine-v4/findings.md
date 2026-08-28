# Findings & Decisions

<!-- 记录执行中发现的问题/事实。需求与设计决策见 OpenSpec change，不在此重复。 -->

## Requirements

需求唯一来源：
- 需求输入：`docs/exploration/react-engine-v4.md`
- OpenSpec specs（验收标准 WHEN/THEN/AND）：`openspec/changes/react-engine-v4/specs/agent-runtime/spec.md`
- OpenSpec tasks（唯一正式任务）：`openspec/changes/react-engine-v4/tasks.md`

（R1 截断可配置 / R2 令牌剥离 / R3 MCP 提速 / R4 步骤可见 / R5 role:tool 回填 —— 细节不在此重述。）

## Research Findings

- **F1（tool_result_clip 现状缺口）**：`tool_result_clip` 目前只有模型列 + runtime 使用，缺三处接线——① `routers/agent.py` `AgentBody` 未声明（API 写不进）；② `startup.py` 无 `ALTER TABLE`（存量库无列）；③ `Agents.vue` 未暴露。对照字段 `mcp_soft_circuit` 三处齐全（agent.py:33/232、startup.py:25、Agents.vue:116/488/607/664）。
- **F2（MiniMax role:tool 支持）**：MiniMax OpenAI 兼容接口支持 `role:tool` + `tool_call_id`，多轮要求完整回填 `tool_calls`。严格校验：assistant 带 tool_calls 时 content 非 null、不 emit `strict`/`tool_choice`、单条 system。现状已满足（`build_tool_schemas` 不 emit strict/tool_choice、content 不会为 null、system 已合并）。

## Technical Decisions

（执行中产生的新决策记录于此；已锁定的设计决策 D1–D9 见 `openspec/changes/react-engine-v4/design.md`。）

| Decision | Rationale |
|----------|-----------|
| | |

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| | |

## Resources

- `openspec/changes/react-engine-v4/`（proposal / design / specs / tasks）
- `docs/exploration/react-engine-v4.md`（需求输入）
- `docs/react-engine.md`（引擎内部地图，现状）
- MiniMax OpenAI 兼容 API：https://platform.minimaxi.com/docs/api-reference/text-openai-api
