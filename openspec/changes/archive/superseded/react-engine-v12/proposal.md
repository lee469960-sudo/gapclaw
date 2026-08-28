## Why

Agent 属性面板无法绑定 LLM 组：`apps/web/src/views/Agents.vue:598` 的 `llms.value = (data.llms || []).filter((l) => l.type === 'llm')` 把 `type:"group"` 的 LLM 全滤掉了，模型下拉框只显示单模型。

但后端与运行时**早已就绪**：

- `routers/agent.py:122-126` 的 `refs` 接口返回全部 LLM 资源（含组），无 type 过滤；
- `routers/agent.py:151` `_validate_refs` 只校验 `llm_id` 存在，不查 type，绑定组合法；
- 运行时 `chat_completion` 的 group failover（v10 D6）+ 环检测（v11）已就绪，`llm_name` 展示（`agent.py:82-86`）按 id 解析、组名可正常显示。

所以这是「Agent 绑定 LLM 组 → 多模型降级（v10 529 兜底策略）」链路的**最后一块拼图**：后端、运行时、校验全通，唯独 UI 没放行，用户实际配不出来。纯前端修复，后端零改动。

## What Changes

- **下拉框包含组**：去掉 `Agents.vue:598` 的 `type === 'llm'` 过滤，`type:"group"` 的 LLM 也进入模型下拉框。
- **视觉区分**：用 `el-option-group` 分「单模型」/「模型组」两组，不改 label 字符串。
- **默认模型**：`defaultLlmId()`（`Agents.vue:634-637`）保持优先单模型（先 `MinMax`、再首个单模型），避免默认选中组。

## Capabilities

### New Capabilities

- `agent-config`: Agent 配置面契约——属性面板的模型绑定（含 LLM 组）、配置项展示与保存。

### Modified Capabilities

（无）

## Impact

- `apps/web/src/views/Agents.vue`（模型下拉框去过滤 + `el-option-group` 分组 + `defaultLlmId` 优先单模型）
