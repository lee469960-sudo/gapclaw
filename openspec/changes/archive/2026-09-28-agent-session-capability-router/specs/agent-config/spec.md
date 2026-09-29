## ADDED Requirements

### Requirement: MCP 和 Skill 配置支持可选 routing hints

Agent configuration SHALL support optional routing hints for MCP and Skill capabilities. Hints MAY include `enabled`, `keywords`, `aliases`, `tags`, and `description`. Existing capability `name`, `tags`, and `description` remain valid route metadata and MUST continue to be used by existing runtime paths. Global capability routing hints SHALL provide defaults where present, and an Agent binding MAY override or extend those defaults. Merged routing hints SHALL be used only by Agent session route matching and MUST NOT change connection credentials, executable permissions, MCP transport configuration, Skill content, or existing preflight authorization.

#### Scenario: 全局 routing metadata 可被绑定覆盖合并

- **WHEN** a capability defines global routing metadata and an Agent binding defines routing overrides
- **THEN** the effective routing metadata is computed deterministically
- **AND** binding-level `enabled` and `description` override global values when present
- **AND** binding-level keywords, aliases, and tags are merged with global keywords, aliases, and tags without duplicates

#### Scenario: Routing metadata 不改变执行权限

- **WHEN** a user updates routing keywords, tags, description, or enabled state
- **THEN** the system does not grant MCP call permission, Skill load permission, credentials, transport access, or tool execution rights
- **AND** existing authorization and preflight checks remain required before execution

#### Scenario: 缺少 routing hints 不移除既有 MCP 路由资格

- **WHEN** a bound callable MCP has no optional routing hints
- **THEN** the system does not require migration or generated keywords before it can run
- **AND** the MCP remains eligible for existing MCP semantic routing, route validation, and empty-route fallback
- **AND** the absence of hints only limits extra hint-based Candidate Capability injection

### Requirement: Routing metadata 管理必须兼容存量能力

The system SHALL NOT automatically rewrite user-created MCP or Skill routing hints from tool directories, prior messages, or model guesses. Built-in or seeded capabilities MAY be given hand-authored routing hints so the router can be verified. Existing Agent configurations without routing hints SHALL continue to load, save, and run with their previous semantics, including existing use of name/tags/description metadata.

#### Scenario: 存量 Agent 无需迁移即可运行

- **WHEN** an existing Agent has bound MCPs or Skills without routing metadata
- **THEN** the Agent can still be loaded, edited, and run
- **AND** the system does not require the user to add routing metadata before normal conversation can continue

#### Scenario: 用户能力不会被自动生成关键词

- **WHEN** a user-created MCP or Skill lacks routing metadata
- **THEN** the system does not automatically infer or persist new keywords from its tool list, prior messages, or model output
- **AND** operators may add explicit routing metadata later

#### Scenario: 内置能力可提供手工 routing metadata

- **WHEN** a built-in or seeded MCP / Skill is installed with hand-authored routing metadata
- **THEN** the Agent session Capability Router may use that metadata when the ability is bound and enabled
- **AND** the metadata remains separate from secrets, transport settings, tool schemas, and Skill content

### Requirement: Routing metadata and route traces are observable without exposing secrets

Agent configuration and session debug surfaces SHALL provide minimal read-only visibility into effective routing metadata and route traces needed to explain why a capability was or was not considered. These surfaces MUST redact secrets, credentials, system prompts, full tool directories, unauthorized global capability names, and internal Chain of Thought. Editable routing UI is optional for this phase and MUST NOT be required for the router to function.

#### Scenario: 调试视图显示路由原因

- **WHEN** an operator inspects a routed Agent session message
- **THEN** the system can show route mode, matched keywords or tags, candidate counts, and blocked or missing reasons
- **AND** the view does not reveal credentials, hidden global ability names to unauthorized users, or full internal prompts

#### Scenario: 配置视图可只读展示有效 routing metadata

- **WHEN** an operator views an Agent's bound MCPs or Skills
- **THEN** the system may show the effective routing enabled state, keywords, tags, and description
- **AND** the absence of an editing UI does not block backend routing behavior
