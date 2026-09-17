# Findings: Capability-Aware React Routing

## Initial findings

- The previous fast-path classifier used fixed operation keywords and did not inspect bound MCP metadata.
- `AgentRuntime._is_conversational` intentionally treats bindings as capabilities, not proof that every message is a task.
- Existing MCP semantic routing already receives MCP name, tags, and description, but only after the request enters the modular path.
- `classify_request("帮我看看okx当前持仓")` previously returned `chat`; explicit operation wording such as `查询` returned `task`.
- The required behavior is metadata-aware entry routing without loading MCP tools or opening MCP connections.

## Constraints

- Preserve existing MCP authorization, LLM semantic selection, and lazy `tools/list` behavior.
- Do not hardcode `okx-trader` or another single MCP name.
- Keep pure conceptual questions such as “什么是持仓” in `chat` mode.
- `tasks.md` remains the sole formal task source.
