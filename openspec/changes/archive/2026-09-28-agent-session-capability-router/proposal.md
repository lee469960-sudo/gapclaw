## Why

Agent 会话已有 MCP / Skill 相关路由雏形：入口模式判定会读取绑定 MCP 元数据，ReAct 内也已有 `mcp_routing.py` 的多 MCP 语义路由、空选择回退、补选和审计。但实际使用中仍出现“已绑定能力却总是匹配失败”的问题：普通会话与需要能力的请求区分不稳定，MCP 关键词/别名/中文短语没有可靠进入现有匹配路径，Skill 只按已加载名称进入模式判断，导致请求可能直接落入 chat 快速路径或 MCP 路由低置信度。

本 change 不是新增一套独立路由系统，而是在现有 Agent Runtime、`execution_policy`、`mcp_routing.py` 和 Skill 加载机制上做收敛优化：对照已有实现修复匹配失败，补齐可配置路由提示和可审计 trace，让 ReAct 在需要时拿到候选能力，但最终执行仍走现有授权、preflight、MCP 惰性发现和 Skill 机制。

## What Changes

- 扩展现有入口模式判定和 MCP 候选构建，使当前 Agent 已绑定 MCP / Skill 的有效路由提示可以用于区分 `normal_chat`、`capability_candidate`、`explicit_capability_request`、`missing_capability` 和 `blocked_capability`。
- 为 MCP / Skill 增加可选 routing hints（如 `keywords`、`aliases`、`tags`、`description`、`enabled`），并继续把现有 `name`、`tags`、`description` 作为基础路由元数据；不要求存量能力迁移。
- 修复“绑定能力匹配失败”场景：显式名称/别名、关键词、中文短语、标签和动作意图能进入现有模式判定与 MCP 路由上下文；Skill 也能作为候选提示进入 ReAct。
- 普通聊天仍不注入 Candidate Capabilities，不扩大 tool list，不加载 Skill，不连接 MCP，不改变 chat 快速路径。
- 能力相关输入只把 TopK 候选以结构化 advisory context 注入 ReAct；ReAct 和现有 preflight / 权限校验仍保留最终执行决策。
- 显式能力触发支持 `@能力名`、能力名/别名直呼、`使用 <能力名>` 等形式，但不得绕过绑定、enabled 或权限边界。
- 路由优化不执行 MCP / Skill、不授权、不读取 Skill.md、不抽取工具参数；只发现“可能需要哪些能力”。
- 增强脱敏 route trace，包括 route mode、命中词、候选、blocked/missing 原因、降级原因和现有 MCP 路由选择结果，供会话调试和历史排查使用。
- 保持与既有多 MCP LLM 语义路由兼容：现有 `mcp_routing.py` 仍是 MCP 惰性发现前的权威选择路径；缺少 routing hints 的已绑定可调用 MCP 仍必须参与既有 MCP 候选和空选择回退。

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `agent-runtime`: 优化现有入口模式判定、MCP 路由候选与 ReAct 上下文候选注入，修复绑定 MCP / Skill 匹配失败，并增加降级语义和 trace 要求。
- `agent-config`: 为 MCP / Skill 能力及 Agent 绑定提供可选 routing hints 配置与只读可观察性约束，同时保留现有 `name`、`tags`、`description` 的路由作用。

## Impact

- Affected backend runtime: Agent chat / ReAct runtime message preparation, `execution_policy` capability matching, `mcp_routing.py` candidate prompt/selection, MCP/Skill binding metadata loading, prompt/context shaping, route trace persistence.
- Affected configuration/API: MCP / Skill / Agent binding config serialization may need optional routing hints with merge semantics.
- Affected frontend: conversation debug trace visibility is required; routing metadata display is optional/minimal in this phase.
- Compatibility: existing ordinary Agent conversations, existing MCP preflight, existing lazy MCP LLM routing, empty-route fallback, unbound MCP stops, and Skill loading behavior must remain compatible.
- Security: router output is advisory only; it must not grant capabilities, reveal unauthorized global ability names to ordinary user context, or expose credentials, tool directories, system prompts, or internal routing details.
