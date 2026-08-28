# Design — react-engine-v12

## Context

动机与根因见 `proposal.md`「Why」。这是本仓库第一个 `agent-config` capability 变更，正交于 `agent-runtime`（后端运行时）与 `channels`（TG 入站）——它覆盖 Agent 配置面的 UI 契约。

铁律贯穿：**无任何静态硬门禁**——本变更纯前端展示/绑定层，不引入任何循环级阈值 / 门禁。

现状：
- `Agents.vue:598` 一行 `filter((l) => l.type === 'llm')` 是唯一拦路点。
- 后端 `refs`（`agent.py:122-126`）已返回组；`_validate_refs`（`agent.py:151`）已接受组。
- `defaultLlmId()`（`Agents.vue:634-637`）：`find(name === 'MinMax')` → 兜底 `llms.value[0]?.id`。

## Goals / Non-Goals

**Goals:**
- 模型下拉框同时列出单模型与模型组，且用户能区分两者。
- 绑定组后能保存、能正确显示组名。
- 默认模型仍优先单模型（避免误选组）。

**Non-Goals:**
- 不改后端（refs / 校验 / 运行时均无需改）。
- 不改 LLM 管理页（组的增删改仍在 LLM 管理页，不在本范围）。
- 不改 group failover 语义（v10/v11 已定）。

## Decisions

### D1: 去过滤 + 分组区分

去掉 `:598` 的 `type === 'llm'` 过滤，改为保留全部；渲染时用 `el-option-group` 分「单模型」/「模型组」两组。
- **为何**：组与单模型语义不同，混在一个平铺列表里用户分不清；`el-option-group` 原生分组、不改 label 字符串，不影响 `defaultLlmId` 按名搜 `MinMax`。

### D2: 默认优先单模型

`defaultLlmId()` 保持「先 `MinMax`、再首个单模型」；若列表为空再退回任意项。
- **为何**：绑定组是显式选择，不该成为新建 Agent 的隐式默认；单模型作为默认更符合直觉。

### D3: 后端零改动

绑定组的合法性、组名解析、运行时 failover 均已就绪，本变更不动 `routers/agent.py` / `llm_client.py`。

## Risks / Trade-offs

- [风险] 组与单模型同名时用户仍可能混淆。→ 缓解：`el-option-group` 分组标题已区分；名称唯一性属 LLM 管理页职责，不在本范围。
- [风险] 绑定组后，组的 `max_context_tokens` / `max_output_tokens` 等字段取自叶子成员而非组自身（运行时现状），用户在组上配的参数不生效。→ 缓解：本变更仅放行绑定，不改变运行时取值语义；如需「组级参数」后续单列需求。

## Open Questions

- 是否需要在绑定组时给用户提示「组内降级：成员失败自动切下一个」。
- 是否需要「组级参数」覆盖（组自身 max_context_tokens / timeout 等），当前取叶子成员。
