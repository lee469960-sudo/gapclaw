## Context

See `proposal.md` for motivation. Current Agent Runtime already has several related mechanisms:

- mode selection for `chat` / `task` / `tool` / `human_wait`;
- MCP preflight and unbound-resource stops;
- `_bound_mcp_capability_hints()` and `classify_request(... capability_hints, skill_names ...)` for entering the task path;
- `mcp_routing.py` for bound MCP candidate construction, LLM semantic MCP routing before `tools/list`, empty-route fallback, supplement routing and audit events;
- Skill loading paths controlled by existing runtime behavior (`load_skill_mds`, `SKILL_MD`, `RUN_SKILL`);
- conversation progress and message/run metadata for runtime observability.

This change optimizes those existing paths. It must not create a parallel router that competes with `mcp_routing.py`, and it must not replace existing MCP lazy routing or preflight semantics. Current specs require authorized bound MCPs to remain eligible for existing MCP routing even when ability metadata is incomplete.

## Goals / Non-Goals

**Goals:**

- Fix existing route matching failures by extending the current mode classifier and MCP route metadata with normalized keywords, aliases, tags, names and action-intent signals.
- Make candidate MCP / Skill suggestions available to ReAct only when the current message likely needs a bound ability.
- Keep ordinary chat context clean: no Candidate Capabilities block, no Skill load, no MCP connection, no tool-list expansion.
- Provide optional routing hints for MCP / Skill defaults and Agent binding overrides while preserving existing `name` / `tags` / `description` semantics.
- Persist sanitized route traces for debugging why a capability was or was not considered.
- Preserve current authorization, preflight, MCP lazy discovery, Skill loading, and ordinary conversation behavior.

**Non-Goals:**

- No LLM-based route classifier in this phase.
- No vector search, embeddings, personalized learning, automatic keyword generation, or automatic modification of user-created capabilities.
- No global or Agent-level feature switch; participation is naturally constrained by routing metadata, binding, enabled state, and existing permission checks.
- No tool parameter extraction or tool planning in the router.
- No automatic Skill.md loading in the router.
- No replacement of existing multi-MCP LLM semantic routing or directory cache behavior.
- No dynamic UI rendering for candidates beyond optional/debug observability.

## Decisions

### 1. Extend the existing route path before ReAct context assembly

The capability decision should run where the runtime already decides whether a request is conversational: after the current Agent, user, bindings, and message are resolved but before ReAct receives its final prompt/context. Existing functions such as `_bound_mcp_capability_hints()`, `classify_request()`, and `mcp_routing.build_mcp_route_candidates()` should be extended rather than bypassed.

Alternatives considered:

- Run inside ReAct as a model step. Rejected because it would rely on model behavior and blur discovery with execution.
- Run after MCP discovery. Rejected because the goal is to avoid unnecessary discovery and context pollution.
- Add a brand-new router service that ignores current MCP routing. Rejected because the existing implementation already contains binding, candidate and fallback semantics that must remain authoritative.

### 2. No new feature switch

The optimization is part of the existing Agent Runtime path. It is effectively inert for ordinary chat, and it must not require users to enable a new switch. Existing Agents with no new routing hints continue to use existing behavior: bound MCPs still participate in MCP semantic routing and empty-route fallback, and Skills still load through existing mechanisms.

Alternatives considered:

- Add global and Agent switches. Rejected by product direction and because it would add another rollout/debug dimension.
- Default all bound abilities into Candidate Capability injection. Rejected because it would pollute ordinary chat and recreate the current ambiguity. This does not affect existing MCP candidate eligibility, which still includes all bound callable MCPs.

### 3. Routing hints are separate from execution configuration

Use optional routing hints such as `enabled`, `keywords`, `aliases`, `tags`, and `description`. These hints are advisory metadata, not transport configuration, auth state, Skill content, or tool schema. Existing `name`, `tags`, and `description` remain first-class base metadata for route matching and prompts. Effective routing hints are computed from global capability defaults plus Agent binding overrides where the current config model supports them.

Suggested merge semantics:

- binding `enabled`, when present, overrides global `enabled`;
- binding `description`, when non-empty, overrides global `description`;
- binding `keywords`, `aliases`, and `tags` are unioned with global lists and deduplicated;
- absent hints mean "no extra hint-based Candidate Capability injection", not "unauthorized" and not "remove legacy runtime behavior".
- for MCPs, absence of routing hints MUST NOT remove the MCP from existing `build_mcp_route_candidates()` output when it is bound and callable.

### 4. Deterministic route assistance plus existing MCP LLM route

The first phase uses fixed, testable matching to decide whether to enter task/tool flow and what advisory Candidate Capabilities to inject:

- explicit id, name, or alias reference (`@name`, direct name mention, `使用 <name>`) is strongest;
- keyword hits outrank tag hits;
- action-intent terms add a bonus;
- descriptions can support existing capability matching and MCP route prompts, but Candidate injection should require stronger signals such as explicit references, aliases, keywords, tags, or action intent;
- short English keywords require token-boundary matching;
- Chinese keywords can use substring matching;
- disabled matches become blocked trace entries, not candidates.

This makes routing explainable and cheap. Once ReAct enters an MCP path, the existing LLM semantic MCP router remains responsible for selecting the MCP subset from all bound callable candidates.

### 5. Candidate context is advisory and structured

When candidates exist, inject both structured data and a short instruction. The instruction must say candidates are suggestions and ReAct may ignore them when a normal answer is sufficient. The structured shape should include route mode, candidate type, id/name, score, hit terms, description, and reason. It must not include full tool directories, secrets, credentials, full prompts, or Chain of Thought.

The router must not directly enlarge executable tool lists. Actual execution remains governed by existing MCP / Skill mechanisms. For MCP specifically, advisory candidates may be included in the user-visible/debug context, but MCP `tools/list` discovery still happens only for the selected MCPs returned by existing `mcp_routing.py` plus its fallback/supplement rules.

### 6. Missing and blocked capabilities degrade without leakage

For missing capability intent, ordinary ReAct context can say only that the current Agent is not configured with a relevant ability. It must not enumerate unauthorized global abilities. Operator/debug trace may retain a sanitized internal reason where permitted.

For blocked capability intent, the disabled capability is excluded from candidates and recorded as blocked in trace.

### 7. Trace persistence uses existing progress/metadata surfaces

Store route traces in existing conversation progress/events and message or run metadata. Do not add a new trace table in this phase. Debug UI can consume these traces without changing core chat UX.

### 8. Existing MCP routing remains authoritative

Capability Router candidates can help ReAct choose a path, but MCP lazy discovery, MCP LLM semantic routing, preflight, unbound-resource stops, empty-route fallback, supplement routing, directory cache rules, and tool dispatch remain the authoritative execution path. This avoids conflict with existing `agent-runtime` requirements that keep authorized bound MCPs eligible for routing even when descriptions/tags are incomplete.

## Risks / Trade-offs

- [False positive capability candidates] → Keep normal chat zero-pollution unless deterministic intent and metadata match; make candidate context advisory and let ReAct ignore it.
- [False negative capability routing] → Reuse existing name/tags/description, add explicit id/name/alias and configured keyword triggers, and record trace for diagnosis.
- [Global capability leakage] → Do not inject unauthorized global names into ordinary context; restrict detailed missing reasons to sanitized operator/debug trace.
- [Conflict with existing MCP routing] → Treat router as candidate-context injection only; preserve current MCP lazy routing/preflight behavior.
- [Metadata drift or malformed config] → Validate and sanitize routing metadata; degrade router failures to `normal_chat` with trace.
- [Prompt bloat] → Apply MCP Top 5 / Skill Top 3 caps and omit candidates for `normal_chat`.

## Migration Plan

1. Audit current `execution_policy`, `_bound_mcp_capability_hints()`, `mcp_routing.py`, Skill loading and related tests to reproduce the known match-failure cases.
2. Add optional routing hint support in config serialization/storage without requiring existing Agents to migrate.
3. Extend existing route helpers with normalized matching, alias/keyword handling and Skill candidate hints.
4. Add Candidate Capability advisory context and trace persistence using existing progress/event and metadata channels.
5. Add debug/read-only surfaces for route trace as needed.
6. Verify current MCP preflight, unbound-resource stops, lazy MCP routing, empty-route fallback, Skill loading, normal chat, scheduled-task context, and ordinary Agent behavior remain compatible.

Rollback is straightforward: remove or bypass the hint-based matching and Candidate Capability injection while leaving persisted routing hints unused. Because routing hints are advisory and separate from execution config, persisted hints can remain without changing runtime permissions.
