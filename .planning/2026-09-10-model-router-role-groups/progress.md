# Progress Log: model-router-role-groups

## Session: 2026-09-10

### Planning initialization

- **Status:** complete
- Actions taken:
  - Read the change proposal, design, all four delta specs, and `tasks.md`.
  - Created a dedicated Planning with Files directory and mapped OpenSpec task IDs to execution phases without redefining requirements.
  - Confirmed every OpenSpec task remains unchecked and no implementation or test execution has occurred for this change.
  - Set this change as the active persistent plan.
  - Inspected repository status and recorded the pre-existing unrelated worktree boundary.
- Files created/modified:
  - `.planning/2026-09-10-model-router-role-groups/task_plan.md`
  - `.planning/2026-09-10-model-router-role-groups/findings.md`
  - `.planning/2026-09-10-model-router-role-groups/progress.md`
  - `.planning/.active_plan`

### Phase 1: Routing data model and compatible configuration API

- **Status:** in_progress
- Current task: OpenSpec 1.1.
- Discovery recorded: startup uses idempotent schema upgrades; existing LLM group membership and Agent `llm_id` must remain untouched by the new routing persistence.
- Completed task 1.1:
  - Added leaf `routing_capabilities`, role-group, routing-policy, and versioned route-decision persistence, plus nullable Agent policy binding.
  - Added idempotent startup migration for pre-existing `llm_resources` and `agents` tables; new routing tables are created through SQLAlchemy metadata.
  - Verified legacy group membership and Agent `llm_id` survive an upgrade unchanged, and new routing records persist.
- Completed task 1.2:
  - Added capability and role-group CRUD endpoints with leaf-only, role, React-runtime, and multimodal-media validation.
  - Kept legacy `type=group` resources outside capability editing and role-group membership.
  - Verified invalid/incompatible members are rejected while existing legacy membership remains unchanged.
- Completed task 1.3:
  - Added routing-policy CRUD with direct-leaf Router LLM validation, default-role mapping validation, and resource visibility checks.
  - Added optional authorized policy binding to Agent configuration and form references; absent policy preserves direct model binding.
  - Verified policy authorization, invalid group Router LLM rejection, and no-policy Agent compatibility.

### Phase 2: Constrained model routing service

- **Status:** in_progress
- Current task: OpenSpec 2.1.
- Discovery recorded: AgentContext is immutable and existing transport exceptions/circuit state can support later pre-output fallback classification. No task-2 implementation has been marked complete.
- Completed task 2.1:
  - Added deterministic candidate construction from explicit policy role groups and leaf capability metadata.
  - Records exclusion reasons for unavailable groups/models, authorization, disabled state, role/runtime/modality mismatch, context insufficiency, and active provider throttling.
  - Verified selection and exclusion behavior without model-name routing rules.
- Completed task 2.2:
  - Added a dedicated Router LLM structured JSON request and candidate-bounded result validation.
  - Invalid JSON, router errors, and out-of-set results fall back only to the eligible configured default role; the Router LLM is excluded from execution candidates.
  - Verified valid selection, malformed output, timeout, self-recursion prevention, and default-only fallback.
- Completed task 2.3:
  - Added versioned `ModelRouteDecision` persistence with redacted candidate/exclusion metadata and a shared frozen-leaf resolver.
  - Added loop-state slots for the persisted decision and frozen LLM id, ready for runtime integration.
  - Verified one task resolves the same frozen leaf and a different session cannot reuse it.

### Phase 3: Standard/React runtime integration and safe degradation

- **Status:** in_progress
- Current task: OpenSpec 3.1.
- Implemented the pre-context policy route and frozen selected leaf for Standard Agents; no-policy and focused runtime-path tests remain before task completion.

## OpenSpec Task Completion

| Task | Status | Evidence |
|---|---|---|
| 1.1 | complete | `pytest -q tests/test_startup_migrations.py` — 5 passed; legacy members/binding preservation and new record persistence covered. |
| 1.2 | complete | `pytest -q tests/test_model_routing_config.py tests/test_startup_migrations.py` — 7 passed; capability/role validation and compatibility covered. |
| 1.3 | complete | `pytest -q tests/test_model_routing_config.py tests/test_startup_migrations.py tests/test_code_agent_profile.py` — 12 passed; policy and Agent binding compatibility covered. |
| 2.1 | complete | `pytest -q tests/test_model_router.py tests/test_model_routing_config.py` — 4 passed; deterministic candidate/exclusion rules covered. |
| 2.2 | complete | `pytest -q tests/test_model_router.py` — 2 passed; strict structured router parsing and safe fallback covered. |
| 2.3 | complete | `pytest -q tests/test_model_router.py` — 3 passed; decision persistence and frozen resolver covered. |
| 3.1–6.3 | pending | Not started. |

## Test Results

| Test | Expected | Actual | Status |
|---|---|---|---|
| None | No implementation verification is due during planning initialization | Not run | not applicable |
| `pytest -q tests/test_startup_migrations.py` | Fresh and upgrade routing persistence preserves legacy fields | 5 passed | passed |
| `pytest -q tests/test_model_routing_config.py tests/test_startup_migrations.py` | Capability and role-group CRUD validation plus schema compatibility | 7 passed | passed |
| `pytest -q tests/test_model_routing_config.py tests/test_startup_migrations.py tests/test_code_agent_profile.py` | Policy validation, authorized Agent binding and existing Code Profile behavior | 12 passed | passed |
| `pytest -q tests/test_model_router.py tests/test_model_routing_config.py` | Candidate filtering and exclusion reasons without model-name rules | 4 passed | passed |
| `pytest -q tests/test_model_router.py` | Router JSON validation, self-recursion prevention and default-role fallback | 2 passed | passed |
| `pytest -q tests/test_model_router.py` | Persisted route decision and shared task-level frozen model resolution | 3 passed | passed |
| `python -m py_compile app/services/agent_runtime/context.py app/services/agent_runtime/runtime.py && pytest -q tests/test_model_router.py tests/test_model_routing_config.py` | Runtime route integration compiles without regressing routing/configuration behavior | 6 passed | passed |
| `pytest -q tests/test_model_router.py tests/test_model_routing_config.py` | A Standard Agent with a policy enters the loop with its routed frozen leaf; an Agent without a policy retains its direct leaf | 7 passed | passed |
| `python -m py_compile app/services/model_router.py app/services/agent_runtime/context.py app/services/agent_runtime/runtime.py && pytest -q tests/test_model_router.py tests/test_model_routing_config.py tests/test_react_engine_v6.py` | Ordered pre-output-only fallback succeeds for timeout/transport/throttle failures and preserves existing LLM failure thresholds | 19 passed | passed |
| `python -m py_compile app/services/model_router.py app/services/agent_runtime/runtime.py && pytest -q tests/test_model_router.py tests/test_model_routing_config.py tests/test_react_engine_v6.py` | Attachment metadata selects matching media candidates; text mentioning media stays text-only | 21 passed | passed |
| `python -m py_compile app/services/agent_runtime/loop_state.py app/services/agent_runtime/context.py app/services/agent_runtime/runtime.py app/routers/agent_chat.py && pytest -q tests/test_model_router.py tests/test_model_routing_config.py tests/test_react_engine_v6.py` | Redacted model-route events survive the task stream and persisted chat execution history | 22 passed | passed |
| `pytest -q tests/test_code_agent_profile.py tests/test_code_agent_control_plane_ui.py tests/test_code_agent_standard_compat.py` | Claude Code keeps its single compatible LLM and rejects role-policy substitution at save and execution boundaries | 81 passed | passed |
| `pytest -q tests/test_model_router.py tests/test_rag.py` | Embedding client/RAG behavior remains independent and embedding resources cannot become route candidates | 16 passed | passed |
| `pytest -q tests/test_react_engine_v11.py tests/test_model_routing_config.py` | LLM capability editing persists leaf metadata and clears it for legacy groups | 14 passed | passed |
| `npm run build` (in `apps/web`) | LLM management capability editor and eligibility/remediation display compile | passed | passed |

## Error Log

| Timestamp | Error | Attempt | Resolution |
|---|---|---:|---|
| 2026-09-10 | Active-plan update initially assumed a path-format pointer | 1 | Read the name-only pointer and applied a targeted update. |
| 2026-09-10 | First task-1.1 patch used two separate updates for `models.py` | 1 | Combined it into one targeted file update; code and tests then passed. |
| 2026-09-10 | New role group was added to the ORM session before validation, causing an autoflush integrity failure | 1 | Delay creation until all member validation succeeds; regression tests pass. |
| 2026-09-10 | Router candidate test reused its Router LLM as the selected worker | 1 | Use a dedicated Router resource; self-reference is correctly excluded. |
| 2026-09-10 | Route-decision dataclass field preceded a required field | 1 | Moved it after `sandbox`; import and tests pass. |
| 2026-09-10 | Focused test command used `apps/api/` paths while already in the API directory, so pytest found no tests | 1 | Re-ran with `tests/...` paths; 7 tests passed. |
| 2026-09-10 | New runtime-state initialization assumed every lightweight test context had routing fields | 1 | Read new fields with `getattr(..., default)`; legacy runtime regression tests pass. |
| 2026-09-10 | Chat-step slimming removed the new structured route fields before history/API rendering | 1 | Added a strict whitelist for model-route detail in persistence and history serialization; regression test passes. |
| 2026-09-10 | Combined API/Web validation invoked `npm run build` from `apps/api`, which has no package.json | 1 | Re-ran the build from `apps/web`; it passed. |

### OpenSpec task 3.1 — completed

- Code: Standard/React execution resolves a bound routing policy before `AgentContext` and stores the resulting decision ID with the frozen leaf model; unbound Agents retain `agents.llm_id`.
- Tests: `pytest -q tests/test_model_router.py tests/test_model_routing_config.py` — 7 passed.
- Confirmation: the focused runtime test intercepts the loop boundary and proves the policy route reaches it as `worker` with a decision ID, while the no-policy path reaches it as `direct` with no decision.

### OpenSpec task 3.2 — completed

- Code: role-group fallback order is explicit and rechecked against leaf, authorization, capability, context, and health gates. The loop may switch exactly once only on its first pre-output, pre-tool timeout, transport, or provider-throttle failure; it records the fallback as the new frozen route decision.
- Tests: `python -m py_compile app/services/model_router.py app/services/agent_runtime/context.py app/services/agent_runtime/runtime.py && pytest -q tests/test_model_router.py tests/test_model_routing_config.py tests/test_react_engine_v6.py` — 19 passed.
- Confirmation: timeout, transport and throttle paths reach only the configured fallback; a successful native-content tool call followed by an error remains on the original model and executes its tool once.

### OpenSpec task 3.3 — completed

- Code: `required_modalities_for_message` derives image/audio/video requirements only from structured attachment descriptors, and policy candidate/fallback construction uses those requirements.
- Tests: `python -m py_compile app/services/model_router.py app/services/agent_runtime/runtime.py && pytest -q tests/test_model_router.py tests/test_model_routing_config.py tests/test_react_engine_v6.py` — 21 passed.
- Confirmation: an image attachment exposes only the configured vision candidate; “please optimize image processing” without an attachment exposes only the configured text candidate.

### OpenSpec task 3.4 — completed

- Code: initial model decisions and bounded fallbacks emit default-collapsed `model_route` execution steps. Their payload is an explicit whitelist of decision, policy/version, role, frozen model, filtered candidate IDs/reasons, failure class, and timing; chat persistence and history responses preserve that same whitelist.
- Tests: `python -m py_compile app/services/agent_runtime/loop_state.py app/services/agent_runtime/context.py app/services/agent_runtime/runtime.py app/routers/agent_chat.py && pytest -q tests/test_model_router.py tests/test_model_routing_config.py tests/test_react_engine_v6.py` — 22 passed.
- Confirmation: the execution-history test injects a decision containing a full prompt, key, and unselected tool output, then proves none reaches the persisted route step.

### OpenSpec task 4.1 — completed

- Code: Code Profile rejects `routing_policy_id` during Agent create/update, and the runtime rejects legacy/bypassed Code Agents with a policy before any execution or LLM substitution.
- Tests: `pytest -q tests/test_code_agent_profile.py tests/test_code_agent_control_plane_ui.py tests/test_code_agent_standard_compat.py` — 81 passed.
- Confirmation: existing Claude Code group/non-compatible binding coverage remains green; new tests prove both save-time and runtime React-Code boundaries.

### OpenSpec task 4.2 — completed

- Code: routing continues to admit only `type=llm` leaf resources, while the RAG embedding client remains exclusively configured through `EMBEDDING_*` settings.
- Tests: `pytest -q tests/test_model_router.py tests/test_rag.py` — 16 passed.
- Confirmation: a deliberately injected `type=embedding` role-group member is excluded as `not_leaf_model`; RAG regression tests still pass.

### OpenSpec task 5.1 — completed

- Code: LLM management persists leaf routing metadata and presents enabled roles, modalities, and runtime declarations. Cards provide an automatic-routing eligibility tag or a precise remediation reason; legacy groups remain ineligible.
- Tests: `pytest -q tests/test_react_engine_v11.py tests/test_model_routing_config.py` — 14 passed; `npm run build` in `apps/web` — passed.
- Confirmation: API regression proves a leaf persists capabilities and conversion to a group clears them; the production web build compiles the editor and status display.

### OpenSpec task 5.2 — completed

- Code: added a distinct Model Routing page and RBAC menu entry. The page separately manages role-model groups (preferred/fallback leaves, budget, timeout) and policies (Router leaf, group mappings, default role); legacy LLM groups are absent from the new surface.
- Tests: `npm run build` in `apps/web` — passed; `pytest -q tests/test_model_routing_config.py tests/test_code_agent_control_plane_ui.py` — 75 passed.
- Confirmation: the configuration API validates leaf/role/router/default-role constraints, and the web bundle contains the new ModelRouting chunk.

### OpenSpec task 5.3 — completed

- Code: Agent editing now exposes an optional routing-policy selector alongside the direct LLM selector. Standard Agents may leave it empty; Code Agents see a disabled selector with an explicit direct-compatible-LLM boundary and save an empty policy.
- Tests: `npm run build` in `apps/web` — passed; `pytest -q tests/test_model_routing_config.py tests/test_code_agent_profile.py` — 9 passed.
- Confirmation: routing-policy/no-policy Agent API coverage and Code Profile rejection coverage remain green.

### OpenSpec task 5.4 — completed

- Code: Model Route execution rows now use the same default-collapsed, keyboard-accessible disclosure UI as tool steps. Expanded details render an explicit client-side allowlist covering decision/policy, selected role, frozen model, candidates/exclusions, failure, and elapsed time only.
- Tests: `npm run build` in `apps/web` — passed; `pytest -q tests/test_model_router.py tests/test_code_agent_control_plane_ui.py` — 85 passed.
- Confirmation: the regression fixture verifies the history API retains collapsed routing detail and excludes injected full-prompt/API-key values; the UI checks route steps as expandable and uses the route-detail serializer.

### OpenSpec task 6.1 — completed

- Code: added end-to-end parameterized Standard/React runtime coverage for General, React-Code, Planner, Multimodal, and Fast policy roles. The fixture uses arbitrary leaf names/IDs and only configured capability metadata to expose the permitted candidate.
- Tests: `pytest -q tests/test_model_router.py` — 18 passed.
- Confirmation: each role produces a route decision and task-level frozen leaf matching its configured role group; the Multimodal case requires an actual image attachment.

### OpenSpec task 6.2 — completed

- Code: added an explicit provider-throttled candidate exclusion test and an exact one-tool-execution assertion after a post-tool failure. Also isolated the v17 mocked-transport suite from shared in-memory throttle-circuit state.
- Tests: `pytest -q tests/test_model_router.py tests/test_model_routing_config.py tests/test_startup_migrations.py tests/test_react_engine_v6.py tests/test_react_engine_v11.py tests/test_react_engine_v17.py tests/test_code_agent_profile.py tests/test_code_agent_standard_compat.py tests/test_rag.py` — 69 passed.
- Confirmation: the focused suite covers router failure/default bounds, unhealthy candidate filtering, pre-output-only fallback, direct binding, legacy LLM Group behavior, Code boundary, embedding independence, and no duplicate tool execution.

### OpenSpec task 6.3 — completed

- Code/documentation: added controlled enable/rollback guidance to `docs/react-engine.md`; the Agent API regression attaches then detaches a routing policy and proves the direct `llm_id` remains unchanged.
- Validation: `pytest -q tests` — 1,130 passed, 4 skipped (including startup-migration compatibility coverage); `npm run build` in `apps/web` — passed; `git diff --check` — passed.
- Confirmation: the explicit `apps/api/tests` suite is used because bare `pytest -q` discovers unrelated workspace scripts that require local ClickHouse/services. All scoped backend tests now pass after preserving fail-closed CodeAgent admission and updating compatibility fixtures to the immutable evidence contract.

### Full-suite compatibility repair — completed

- Code: preserved fail-closed CodeAgent admission while moving source-scan rejection before workspace allocation; completed lightweight-FakeDB handling for lazy MCP routing; corrected stale no-duplicate-inference ReAct expectations; and aligned CodeAgent fixtures with sealed snapshot, trusted image digest, and workspace-mapping requirements.
- Tests: `pytest -q tests/test_code_agent_control_plane.py tests/test_code_agent_control_plane_ui.py tests/test_code_agent_policy_layers.py` — 133 passed; `git diff --check` — passed.
- Final validation: `pytest -q tests` — 1,130 passed, 4 skipped; `npm run build` — passed; `git diff --check` — passed.

### OpenSpec verification — passed

- Completeness: `tasks.md` is 19/19 complete; all four delta specs were reviewed against their implementation and test evidence.
- Correctness: candidate gates, bounded fallback, frozen task model reuse, redacted route events, direct-binding compatibility, Claude Code boundary, and configured-role tests map to the seven specified requirements and scenarios.
- Coherence: the separate policy resources, deterministic-before-LLM routing, task-level freeze, and separate UI surfaces match `design.md`.
- Validation: `openspec validate model-router-role-groups --strict` — valid; `git diff --check` — passed. No critical issues or warnings found.

### Documentation follow-up — completed

- Added `docs/model-routing.md`, a Chinese configuration guide covering capability declarations, role groups, routing policies, Agent binding, bounded fallback, rollback, and Code Profile/embedding boundaries.
- Re-verified after the documentation-only change: 19/19 tasks complete; `openspec validate model-router-role-groups --strict` and `git diff --check` pass. The prior full API/Web test evidence remains applicable because no runtime or test source changed.

### OpenSpec archive — completed

- Synced delta requirements into `openspec/specs/agent-config`, `agent-runtime`, and `code-agent-profile`, and created the new `openspec/specs/model-routing` main specification.
- Validation: `openspec validate --specs --strict` — 22 passed, 0 failed; archived change `2026-09-11-model-router-role-groups` passed the archived validation scan; `git diff --check` — passed.
- Archived to `openspec/changes/archive/2026-09-11-model-router-role-groups/`.
- Note: the global archived scan also reports three unrelated pre-existing archived changes with incomplete tasks; none is this change.
