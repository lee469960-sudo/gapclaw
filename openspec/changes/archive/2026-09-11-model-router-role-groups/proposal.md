## Why

当前 LLM 组只是按成员顺序逐个尝试的故障转移，Agent 在整个运行中固定使用一个绑定资源。它不能按任务、输入模态、运行时兼容性、健康状态和预算选择适合的模型，且自动重试可能造成不可复盘的重复推理或工具执行。

## What Changes

- 引入模型能力元数据、角色模型组与独立的 LLM ModelRouter，使路由在受约束的候选模型中选择 General、React-Code、Planner、Multimodal 或 Fast 角色。
- 为模型组提供首选与有序降级配置、预算和超时；保留现有普通 LLM Group 的顺序故障转移语义，避免改变已绑定 Agent。
- 在 Standard/React 运行开始前冻结一个路由决策；仅在尚未产生有效模型输出或执行工具时，对可恢复的基础设施失败执行有界降级。
- 扩展 Agent 与 LLM 管理界面，展示和编辑单模型能力、角色组和路由策略；在会话执行详情中展示脱敏的路由审计。
- 将实际媒体附件作为多模态路由的强制能力门槛；Embedding 继续作为独立全局配置，不进入聊天模型路由。
- 明确 Claude Code 保持绑定单个兼容 LLM 的可复现语义；新增的可路由 React-Code 角色不改变 Claude Code 的模型绑定契约。

## Capabilities

### New Capabilities

- `model-routing`: 模型能力、角色模型组、候选过滤、LLM 路由、冻结降级和脱敏审计。

### Modified Capabilities

- `agent-config`: 允许配置模型能力、角色模型组和路由策略，并让 Agent 选择路由策略而非只选择一个 LLM 资源。
- `agent-runtime`: 在 Standard/React 运行的首次模型调用前选择并冻结模型，限制可恢复失败的降级，并暴露路由事件。
- `code-agent-profile`: 保持 Claude Code 单模型绑定；定义可路由 React-Code 角色与 Claude Code runtime 的边界。

## Impact

- 影响 LLM/Agent 数据模型、迁移、LLM 与 Agent 管理 API 和 Vue 管理界面。
- 影响 `apps/api/app/services/agent_runtime/` 的运行初始化、会话事件及测试；将增加独立模型路由服务和评估覆盖。
- 不新增外部服务依赖；Router LLM 和各模型均复用已配置的单模型资源。Embedding 仍通过现有 `EMBEDDING_*` 配置运行。
