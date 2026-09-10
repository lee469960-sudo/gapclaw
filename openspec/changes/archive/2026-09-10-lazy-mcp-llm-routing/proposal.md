## Why

当前运行时会在开始执行前为 Agent 绑定的每个 MCP 调用 `tools/list` 并注入完整工具目录。这会造成不必要的连接、延迟和上下文占用，也无法基于 MCP 能力描述进行最小化选择。

## What Changes

- 在工具目录发现前增加 LLM 驱动的 MCP 路由步骤：仅以用户请求、运行上下文和候选 MCP 的元数据选择 `0 / 1 / N` 个 MCP。
- 对未选择的 MCP 实施完全惰性加载：不连接、不调用 `tools/list`、不创建目录缓存。
- 支持在已选 MCP 不可达、缺少所需工具或主 Agent 明确需要额外能力时受限补选；单次运行最多补选两次。
- 将路由失败安全降级为无 MCP 选择，禁止回退为全量加载，并在服务端校验路由结果的绑定与访问权限。
- 要求参与自动路由的 MCP 具备有效的能力描述或标签，并记录脱敏的结构化路由审计事件。
- 为惰性选择、跨域多选、失败降级、补选上限和缓存边界增加自动化回归覆盖。

## Capabilities

### New Capabilities

无。

### Modified Capabilities

- `agent-runtime`: 将多 MCP 工具目录发现与分派调整为由 LLM 路由驱动的惰性加载，并定义其安全、缓存与可观测性契约。

## Impact

- 影响 `apps/api/app/services/agent_runtime/` 的系统提示、运行循环和 MCP 工具可用性构建。
- 影响 MCP 元数据校验、运行状态/审计记录和相关 API/UI 提示。
- 影响 MCP 客户端目录缓存与 Agent Runtime 回归测试；不新增外部服务依赖。
