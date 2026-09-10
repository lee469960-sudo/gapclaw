# Findings: model-router-role-groups

## Requirements Source

- The only formal implementation source is [`openspec/changes/model-router-role-groups/tasks.md`](../../openspec/changes/model-router-role-groups/tasks.md).
- The approved requirements consist of the new `model-routing` capability plus deltas for `agent-config`, `agent-runtime`, and `code-agent-profile`.
- All OpenSpec tasks 1.1–6.3 are unchecked at initialization time.

## Design Findings

- Existing `LLMResource` supports a leaf model or a sequential legacy `type=group`; it is not a semantic model router.
- Model selection must be hybrid: deterministic compatibility/health/budget gates first, then an independent direct Router LLM selects only among eligible candidates.
- Each Standard/React task must freeze one leaf model before its first LLM call. Automatic fallback is allowed only for a pre-output, pre-tool, classified infrastructure failure.
- Legacy direct `llm_id` binding and existing LLM Groups remain compatible. Embedding stays under the independent `EMBEDDING_*` configuration.
- Claude Code remains bound to one compatible direct LLM; the new React-Code role applies only to Standard/React execution.
- Route detail must be structured, default-collapsed, and redacted: no full prompt, provider key, or unselected tool output.

## Initialization Notes

- This change introduces no implementation work during planning initialization.
- Every task will be recorded in `progress.md` with code/test evidence before its corresponding checkbox is changed in OpenSpec `tasks.md`.
- The worktree already contains unrelated user changes (`.claude/settings.local.json` and untracked `.schema`); they will be preserved and excluded from this change's implementation work.

## Repository Findings

- Startup schema upgrades use `apps/api/app/startup.py` with idempotent table/column inspection and `ALTER TABLE` additions; new routing tables can be created by SQLAlchemy metadata and Agent policy binding needs a compatible startup column migration.
- Mutable resumable run state is represented by `AgentLoopState`, while persistent task state is stored in `AgentRunState`; versioned route decisions can use a dedicated persistent model without altering legacy LLM group membership.
- Existing focused tests cover startup migration, LLM transport, CodeAgent profile behavior, and agent-runtime observability; task 1.1 should add a narrow migration/persistence test before routing work begins.
- `AgentContext` is frozen, so task-level selected-model state must be kept in mutable loop/run state or a dedicated resolver rather than by mutating `ctx.llm`.
- Existing LLM transport exposes `LLMTransportError`, `LLMProviderThrottled`, and a provider throttle circuit; these are the starting point for the later pre-output-only fallback classifier.
- Standard runtime now resolves a valid Agent routing policy before `AgentContext` creation and freezes the selected leaf in `ctx.llm`; Code Profile is excluded. A focused runtime isolation test remains required before task 3.1 can be completed.

## Issues Encountered

| Issue | Resolution |
|---|---|
| Active-plan pointer stores a change name instead of a plan-file path | Used the established name-only format when activating this plan. |
| Initial task-1.1 patch contained two updates to `models.py` | Combined the updates in one patch; no partial code was written. |
| Role-group creation was staged before a validation query, triggering SQLAlchemy autoflush | Create the ORM row only after all validation queries complete. |
| Adding a defaulted route-decision field before non-default `sandbox` broke dataclass initialization | Place the defaulted field after all required fields. |
| Initial compact ModelRouting Vue template omitted two table-column/form-item closing tags | Corrected the tags; production Vite build passes. |
| Model-route execution events were persisted with a safe detail object but AgentChat expanded only `type=tool` rows | Treat `type=model_route` as an expandable execution item and apply a second client-side allowlist before rendering. |
| Legacy group-throttle tests leave an in-memory circuit open for the shared mocked endpoint, making unrelated v17 tests order-dependent | Add an autouse reset fixture in the v17 mocked-transport test module. |
| Broad `pytest` discovers user workspace data generators and smoke scripts outside `apps/api/tests`, which attempt local ClickHouse/service connections | Use `pytest -q tests` as the backend test command; the test root should be made explicit in a separate test-harness change if desired. |
| Complete API suite initially had cross-capability failures (CodeAgent readiness/baseline, lazy-MCP FakeDB compatibility, and stale ReAct expectations) | User authorized compatibility repair. The production fixes preserve fail-closed CodeAgent admission and direct MCP tool execution for lightweight DB adapters; fixtures now supply required immutable source evidence. |

## Cross-capability Regression Findings

- A published CodeAgent Git configuration is intentionally not execution-ready by itself. A run additionally requires a sealed source snapshot matching the resolved commit and content hash, a trusted image digest, and a valid workspace mapping.
- The control-plane end-to-end test must therefore model the distinct controlled-import step after config publication, rather than bypassing the readiness checks.
- A CodeAgent run audit now includes the frozen `image_digest`; the UI audit regression must assert its exact value rather than treating it as an omitted field.
- Unit tests focused on policy-layer intersections should seed readiness evidence so their asserted policy rejection is reached without weakening the preceding repository/snapshot/image gates.

## Verification Findings

- Strict OpenSpec validation accepts `model-router-role-groups`; its spec-driven schema exposes `proposal`, `specs`, `design`, and `tasks` artifacts only, so verification uses `openspec instructions apply` for artifact context rather than a nonexistent `verify` artifact instruction.
- The seven added requirements are covered by the routing service, Standard runtime integration, Agent/API/UI configuration, Code Profile boundary, and focused regression tests; no spec/design divergence was identified.

## Archive Findings

- Main specifications now carry the four delta-spec changes: additions to `agent-config`, `agent-runtime`, and `code-agent-profile`, plus the new `model-routing` capability.
- The change was archived at `openspec/changes/archive/2026-09-11-model-router-role-groups/`. The archived validation scan confirms this archive is complete; other historical archives retain their own unrelated incomplete-task diagnostics.

## Resources

- `openspec/changes/model-router-role-groups/proposal.md`
- `openspec/changes/model-router-role-groups/design.md`
- `openspec/changes/model-router-role-groups/specs/`
- `openspec/changes/model-router-role-groups/tasks.md`
