## Context

See [proposal.md](proposal.md). The current `LLMResource` supports only a leaf model or a `type=group` resource containing an ordered list of leaf ids. Standard runtime resolves the Agent's `llm_id` once and sends every LLM call to it; a group retries members in order. The existing MCP router is LLM-driven but selects MCPs, not models. Claude Code deliberately rejects groups and non-Anthropic LLMs so a frozen CodeAgent run has a reproducible model/key binding.

## Goals / Non-Goals

**Goals:**

- Add an explicit policy layer between Standard/React task creation and the first LLM invocation.
- Allow independently configured leaf models to become candidates through metadata, not through name-based conditionals.
- Use an LLM only to choose among candidates that have already passed deterministic safety and compatibility gates.
- Preserve direct model binding and legacy LLM Group behavior for existing Agents.
- Make every decision, fallback and exclusion explainable in the existing execution-detail UI.

**Non-Goals:**

- No Agent-to-Agent delegation or parallel sub-agent orchestration in this change.
- No automatic conversion of existing groups, Agents or historical runs.
- No routing of Embedding requests; those remain the global OpenAI-compatible embedding configuration.
- No model substitution inside Claude Code runs, and no full-prompt logging or automatic quality-based reruns after output/tool execution.

## Decisions

### 1. Introduce policy resources instead of overloading legacy LLM groups

Add persistent resources for model capability metadata, role model groups and routing policies. A capability record attaches to exactly one leaf `LLMResource`; a role group contains explicit preferred/fallback leaf ids plus budget/timeout; a policy names one direct leaf Router LLM, the allowed role groups and a default role.

The role enum is `general`, `react_code`, `planner`, `multimodal`, and `fast`. The UI labels are General, React-Code, Planner, Multimodal and Fast. Router LLM, role members, and fallback members must reference leaf resources; legacy `type=group` stays isolated.

Alternative: add fields to the existing JSON `members` list. Rejected because it would conflate the historical sequential-failover contract with role policy and make migration ambiguous.

### 2. Hybrid routing: deterministic candidate gates, LLM classification within the candidate set

`ModelRouter` receives a minimal task summary, declared task context, attachment modality facts, and candidate metadata. Before invoking it, the service filters by: requested runtime, role membership, supported media, context, enabled/healthy state, authorization, and configured budget. It asks the Router LLM for strict JSON `{role, model_id, reason}` and validates both IDs against the filtered candidate set.

Task intent is not encoded as a model-name switch. Deterministic gates protect compatibility; the Router LLM makes the semantic role choice. A malformed/error response falls back only to the policy's explicit default role and its currently eligible preferred member. It never widens to all resources.

Alternative: always route with an LLM and trust its result. Rejected because it can select unsupported media or runtime models and cannot safely bootstrap itself. Alternative: only a rule table. Rejected because it hardcodes evolving task semantics.

### 3. Freeze once per run; downgrade only before side effects

At run initialization, persist a `ModelRouteDecision`/equivalent event and attach the selected leaf model and policy version to run context. Main ReAct calls, completion review, session summaries and MCP semantic routing all consume this frozen resource.

A fallback candidate can be tried once only when the previous request failed due to a classified infrastructure condition before meaningful model output, native tool calls or tool execution. The service records `pre_output=false/true`, tool-call count and tool-execution state. Once any side effect or valid assistant content exists, no automatic model swap is permitted; the normal resumable/error flow applies.

Alternative: route every turn or retry all errors across all group members. Rejected because it breaks continuity, hides model changes and can duplicate reasoning/tool work.

### 4. UI has separate model, role-group and policy surfaces

The LLM management surface gains a model-capability editor and eligibility badges. It presents legacy failover groups separately from role groups. A routing-policy editor selects a Router LLM, role-group mappings, default role, budget, timeout and audit state. Agent configuration adds an optional policy selector while retaining the direct LLM selector for compatibility.

The existing execution-detail/tool-call area adds a collapsed Model Route item. It renders structured audit fields (classification, candidates, exclusions, selected model, fallback, timing and frozen state) and never full prompts, provider keys or hidden candidate tool outputs.

### 5. Claude Code remains direct and deterministic

The new `react_code` role is only eligible in Standard/React execution. Code Profile runs whose effective runtime is Claude Code keep their current single Anthropic `LLMResource` validation, frozen command configuration and fail-closed behavior. The UI disables/labels role-policy binding for that runtime rather than silently ignoring it.

## Risks / Trade-offs

- [Router adds one small LLM request and latency] → use a small dedicated Router leaf, compact structured prompt, task-level cache/freeze, and emit route timing.
- [Incorrect metadata excludes a viable model] → surface eligibility and exact missing capability in configuration UI; retain direct binding escape hatch.
- [Fallback misclassification repeats work] → only classify transport/availability errors as recoverable before output or effects; test all post-output and post-tool boundaries.
- [Provider health is stale] → use existing failure/circuit state as a gate and keep availability state advisory, with audit evidence.
- [Policy changes during a run] → snapshot policy identifier/version and leaf selection into the route decision.

## Migration Plan

1. Add nullable/new routing tables or columns and migrations without changing existing `llm_resources.members` or `agents.llm_id` semantics.
2. Ship APIs/UI with routing disabled unless an Agent explicitly selects a valid policy.
3. Configure leaf capabilities, then role groups, then policy resources; validation prevents incomplete policies from activation.
4. Enable a policy for selected Standard Agents and inspect route audit events and success/latency/error metrics.
5. Roll back by detaching the Agent policy; existing direct `llm_id` execution resumes. Policy data and audit events remain available for diagnosis.

## Open Questions

None. Model names and provider-specific feature claims are intentionally configuration data to be verified when each leaf model is registered, rather than assumptions embedded in routing code.
