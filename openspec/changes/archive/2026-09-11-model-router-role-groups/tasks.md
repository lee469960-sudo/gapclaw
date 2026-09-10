## 1. Routing data model and compatible configuration API

- [x] 1.1 Add migrations and persistence for leaf-model capability metadata, role model groups, routing policies, Agent policy binding, and versioned route decisions; verify fresh and upgrade database migrations preserve existing `llm_resources.members` and `agents.llm_id` values.
- [x] 1.2 Implement validated CRUD/serialization for leaf capabilities and role model groups, including role/runtime/modality compatibility and legacy-group isolation; verify API tests reject groups/non-leaf members and incompatible models while legacy `type=group` tests remain unchanged.
- [x] 1.3 Implement validated CRUD/serialization for routing policies and authorized Agent policy binding; verify tests cover a direct leaf Router LLM requirement, default-role validity, visibility checks, and the no-policy compatibility path.

## 2. Constrained model routing service

- [x] 2.1 Implement candidate construction from explicit policy role groups and capability metadata, with deterministic runtime, media, context, budget, health, and authorization gates; verify unit tests assert excluded candidates and their reasons without model-name routing rules.
- [x] 2.2 Implement the dedicated Router LLM request and strict structured-result validation; verify tests cover a valid in-set role/model decision, malformed output, timeout, self-recursion prevention, and a default-role-only safe fallback that never widens candidates.
- [x] 2.3 Implement route-decision persistence and a shared frozen-model resolver; verify unit tests show the same leaf model is returned for main loop, completion review, session summary, and MCP semantic routing in one task.

## 3. Standard/React runtime integration and safe degradation

- [x] 3.1 Invoke model routing before the first Standard/React LLM call when an Agent has a valid routing policy, while retaining direct `llm_id` execution for Agents without one; verify focused runtime tests cover both paths.
- [x] 3.2 Implement one bounded ordered fallback only for classified pre-output, pre-tool infrastructure failures; verify regression tests allow retry for timeout/rate-limit/circuit failures and forbid reruns after valid content, native tool calls, or executed tools.
- [x] 3.3 Route real media attachments only to matching multimodal candidates and retain text-only routing for media-related words without attachments; verify attachment and text-only scenario tests.
- [x] 3.4 Emit redacted structured route/fallback events into the existing task execution stream; verify tests prove events include decision fields but exclude full prompts, keys, and unselected tool output.

## 4. CodeAgent and embedding compatibility boundaries

- [x] 4.1 Preserve Claude Code single compatible LLM validation and disable or clearly reject role-policy model substitution for its effective runtime; verify existing Claude Code group/non-Anthropic rejection tests and new React-Code boundary tests pass.
- [x] 4.2 Keep embedding requests outside routing resources and preserve `EMBEDDING_*` configuration behavior; verify embedding-client regression tests and a routing test that excludes embedding configuration from candidates.

## 5. Management and execution-detail UI

- [x] 5.1 Extend the LLM management UI to edit leaf capability metadata and display automatic-routing eligibility and remediation reasons; verify the web build and component/API tests cover eligible and ineligible models.
- [x] 5.2 Add distinct role-model-group and routing-policy configuration surfaces, including Router LLM, role mappings, default role, budget, timeout, and legacy-group visual separation; verify the web build and form validation behavior.
- [x] 5.3 Add optional Agent routing-policy selection while preserving direct-model selection for legacy Agents and clear Claude Code boundary messaging; verify UI/API integration coverage for policy, no-policy, and Claude Code cases.
- [x] 5.4 Add a default-collapsed Model Route execution-detail item with expand-to-view redacted decision, filtering, fallback, timing, and frozen-model fields; verify UI regression coverage and that no full prompt/key is rendered.

## 6. End-to-end verification and rollout safeguards

- [x] 6.1 Add end-to-end coverage for General, React-Code, Planner, Multimodal, and Fast role selection using configured metadata rather than model names; verify each task freezes a permitted configured leaf.
- [x] 6.2 Add regression coverage for route failure, unhealthy candidates, fallback boundaries, direct-binding compatibility, legacy LLM Group ordering, and no duplicate tool execution; verify the complete focused API test suite passes.
- [x] 6.3 Run backend and web validation commands and document the controlled rollout/rollback check (attach/detach policy without mutating legacy bindings); verify all commands pass and the migration compatibility check is recorded.
