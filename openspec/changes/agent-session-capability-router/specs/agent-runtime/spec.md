## ADDED Requirements

### Requirement: Agent 会话输入必须先进行能力路由分类

Agent Runtime SHALL extend the existing pre-ReAct route path to classify each Agent session message as one of `normal_chat`, `capability_candidate`, `explicit_capability_request`, `missing_capability`, or `blocked_capability`. The classification MUST reuse the current message, explicit ability references, system action-word rules, current Agent bindings, existing MCP/Skill metadata, and optional routing hints. It MUST NOT use an LLM for this classification, execute tools, connect to MCP servers, call `tools/list`, read Skill.md, extract tool parameters, or grant any permission.

#### Scenario: 普通聊天保持零候选污染

- **WHEN** a user asks a general explanation question that does not contain an explicit ability reference and does not match both routing metadata and action intent
- **THEN** the router classifies the message as `normal_chat`
- **AND** no Candidate Capabilities block is injected into the ReAct context
- **AND** no MCP connection, Skill load, tool directory discovery, or tool-list expansion occurs because of the router

#### Scenario: 关键词和动作意图命中绑定能力

- **WHEN** the current Agent has an enabled bound MCP whose existing name/tags/description or optional routing hints include `clickhouse` and `sql`, and the user asks "帮我看看这个 ClickHouse SQL 为什么这么慢"
- **THEN** the router classifies the message as `capability_candidate`
- **AND** the bound MCP can appear as a Candidate Capability with hit keywords and a confidence score
- **AND** ReAct remains responsible for deciding whether any MCP call is needed

#### Scenario: 显式能力请求成为强候选但不越权

- **WHEN** the user references a capability with `@capability-name`, direct id/name/alias mention, or `使用 <capability-name>`
- **THEN** the router treats the reference as an `explicit_capability_request`
- **AND** only a capability currently bound to the Agent, enabled, and permitted may become a candidate
- **AND** an unbound or disabled explicit reference does not become executable

### Requirement: Candidate Capabilities 必须受绑定、enabled、现有资格和 TopK 约束

Capability Router candidate output SHALL include only MCP / Skill capabilities bound to the current Agent, enabled after applying routing hint merge rules, and still valid under existing permission/preflight eligibility. It SHALL rank advisory candidates using deterministic rules: explicit id/name/alias match outranks keyword hits, keyword hits outrank tag hits, action-intent matches add a bonus, and short English tokens MUST require token-boundary matching. It SHALL return at most five MCP candidates and at most three Skill candidates. Candidate output MUST include capability id/name, type, score, hit keywords/tags/aliases, route reason, and description when available.

This advisory candidate limit MUST NOT shrink the existing MCP semantic-routing candidate pool. Any MCP that is currently Agent-bound and callable MUST remain eligible for `build_mcp_route_candidates()` and empty-route fallback even when it has no optional routing hints.

#### Scenario: Candidate list is bounded and deterministic

- **WHEN** more than five bound MCPs and more than three bound Skills match the same user message
- **THEN** the router returns only the highest-ranked five MCP candidates and three Skill candidates
- **AND** candidates with the same score are ordered deterministically

#### Scenario: Skill candidate does not load Skill content

- **WHEN** a bound Skill matches by keyword or explicit name
- **THEN** the router may include the Skill as a Candidate Capability
- **AND** the router does not read or inject Skill.md content
- **AND** any later Skill loading remains controlled by the existing ReAct / Skill mechanism

#### Scenario: 缺少新 routing hints 的 MCP 仍保留既有路由资格

- **WHEN** a bound callable MCP has no optional routing hints
- **THEN** it may be absent from the new advisory Candidate Capabilities block unless matched by existing id/name/tags/description
- **AND** it still remains a valid candidate for existing MCP semantic routing and empty-route fallback

#### Scenario: Skill candidate does not load Skill content

- **WHEN** a bound Skill matches by keyword, alias, tag, or explicit name
- **THEN** the router may include the Skill as a Candidate Capability
- **AND** the router does not read or inject Skill.md content
- **AND** any later Skill loading remains controlled by the existing ReAct / Skill mechanism

### Requirement: Missing, blocked, and router errors must degrade safely

Capability Router SHALL distinguish missing and blocked ability intent without expanding access. A message that appears to require a known capability area not bound to the current Agent MAY be classified as `missing_capability`, but ordinary ReAct context MUST NOT reveal unauthorized global capability names. A message that matches a bound but disabled ability SHALL be classified as `blocked_capability` and the disabled ability MUST NOT be injected as a candidate. Any router parsing, metadata, or matching error MUST degrade to the existing safe route outcome and record a trace reason; it MUST NOT block the conversation.

#### Scenario: 未绑定能力不泄露全局能力名

- **WHEN** the user asks for a ClickHouse database query and the current Agent has no bound ClickHouse-capable MCP or Skill
- **THEN** the router may classify the message as `missing_capability`
- **AND** ordinary ReAct context only indicates that the current Agent is not configured with a relevant ability
- **AND** it does not expose unauthorized global capability ids, names, connection details, or tool directories

#### Scenario: Disabled bound capability is blocked

- **WHEN** a bound MCP or Skill matches the user message but its merged routing metadata is disabled
- **THEN** the router classifies the match as `blocked_capability`
- **AND** the disabled capability is not injected as a Candidate Capability
- **AND** trace records the blocked reason for operator inspection

#### Scenario: Router failure does not break chat

- **WHEN** routing metadata is malformed or the router raises an internal matching error
- **THEN** Runtime continues as `normal_chat`
- **AND** no Candidate Capabilities are injected
- **AND** a sanitized router error trace is recorded

### Requirement: Capability routing context is advisory and auditable

When the route mode is `capability_candidate` or `explicit_capability_request`, Agent Runtime SHALL inject a structured Candidate Capabilities context plus a short instruction telling ReAct that candidates are advisory and execution remains subject to existing planning, authorization, and preflight checks. The router MUST NOT directly enlarge the executable tool list or bypass existing MCP lazy discovery, MCP route validation, empty-route fallback, supplement routing, unbound-resource stops, Skill loading, or preflight behavior. Runtime SHALL persist sanitized route trace in progress/events and message or run metadata.

#### Scenario: ReAct receives advisory candidates only

- **WHEN** the router finds matching MCP and Skill candidates
- **THEN** ReAct receives a structured candidate context with type, name, score, hit terms, and reason
- **AND** ReAct is instructed that it may ignore candidates when a normal answer is sufficient
- **AND** actual execution still requires the existing bound ability and preflight path

#### Scenario: Existing MCP lazy routing remains authoritative

- **WHEN** ReAct later enters a tool path and MCP discovery or MCP route selection is needed
- **THEN** the existing MCP lazy discovery, LLM semantic routing, route validation, directory cache, and preflight rules still apply
- **AND** the Capability Router candidate list does not by itself connect to, select, authorize, or exclude an MCP from the existing eligible candidate pool

#### Scenario: Route trace is available for debugging

- **WHEN** any Agent session message is routed
- **THEN** Runtime records sanitized trace including route mode, matched terms, candidate ids visible to the current Agent, blocked/missing reasons, and injected candidate count
- **AND** trace excludes full prompts, credentials, tool directories, system prompts, and Chain of Thought
