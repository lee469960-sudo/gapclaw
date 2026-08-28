# Progress Log: code-agent-claude-code-runtime

## Session: 2026-08-26

### Current Status

- **Tracking state:** planning initialized; implementation not started.
- **Current execution phase:** Phase 0 complete.
- **OpenSpec apply state:** 30 tasks total, 0 complete, state `ready`.
- **Validation:** `openspec validate code-agent-claude-code-runtime --strict` → valid.

### Actions completed

#### Planning initialization

- Read OpenSpec proposal:
  - `openspec/changes/code-agent-claude-code-runtime/proposal.md`
- Read OpenSpec design:
  - `openspec/changes/code-agent-claude-code-runtime/design.md`
- Read OpenSpec tasks:
  - `openspec/changes/code-agent-claude-code-runtime/tasks.md`
- Read all delta specs:
  - `specs/code-agent-coding-runtime/spec.md`
  - `specs/code-agent-profile/spec.md`
  - `specs/code-agent-project-policy/spec.md`
  - `specs/code-agent-sandbox-runtime/spec.md`
  - `specs/code-agent-verification/spec.md`
  - `specs/code-agent-operator-ui/spec.md`
- Created Planning with Files directory:
  - `.planning/2026-08-26-code-agent-claude-code-runtime/`
- Created planning files:
  - `task_plan.md`
  - `findings.md`
  - `progress.md`

### Verification performed

- `openspec status --change code-agent-claude-code-runtime --json`:
  - planning artifacts complete.
- `openspec instructions apply --change code-agent-claude-code-runtime --json`:
  - 30 tasks total.
  - 0 complete.
  - state `ready`.
- `openspec validate code-agent-claude-code-runtime --strict`:
  - valid.

### OpenSpec task completion

- OpenSpec tasks 1.1, 1.2 and 1.3 completed after implementation and targeted backend verification.
- Next task: 2.1.

#### Phase 1 implementation: runtime selection and policy freeze

- Implemented `coding_runtime` policy support with default `legacy` and `claude_code` opt-in.
- Added deploy/config feature flag `code_claude_code_runtime_enabled`, defaulting disabled.
- Froze runtime-related execution fields into each CodeAgent run contract:
  - `coding_runtime`
  - `model_config`
  - `runtime_budgets`
  - `allowed_skills`
  - `authorized_mcp_servers`
- Intersected bound Agent skills/MCPs with effective policy before freezing the run contract.
- Added runtime kill-switch scope and enforced it for new CodeAgent runs after runtime policy resolution.
- Verified feature flag rollback affects only new runs and preserves historical `claude_code` run contract evidence.

Changed files:

- `apps/api/app/config.py`
- `apps/api/app/services/code_agent/control_plane.py`
- `apps/api/app/services/code_agent/kill_switch.py`
- `apps/api/tests/test_code_agent_policy_layers.py`
- `apps/api/tests/test_code_agent_control_plane.py`

Verification:

- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_policy_layers.py apps/api/tests/test_code_agent_control_plane.py apps/api/tests/test_code_agent_run_baseline.py apps/api/tests/test_code_agent_standard_compat.py`
  - Result: 70 passed, 43 warnings.

Task evidence:

- 1.1: legacy default and `claude_code` opt-in covered by policy merge/run creation tests.
- 1.2: frozen runtime/model/budget/skill/MCP contract fields covered by run contract tests; policy intersection prevents expansion beyond bound Agent capabilities.
- 1.3: feature flag disabled fallback and runtime kill switch covered by control-plane tests; historical run evidence preservation covered by two-run feature flag test.

#### Phase 2 implementation: trusted runner image CLI pinning

- Updated `deploy/code-agent-runner.Dockerfile` to install pinned Claude Code CLI version `2.1.246` at image build time.
- Disabled Claude Code auto-updater in the runner image via `DISABLE_AUTOUPDATER=1`.
- Added `claude --version` verification during image build.
- Added `claude_code_version` to runner facts for runs whose frozen contract selects `coding_runtime=claude_code`.
- Kept legacy runs compatible: legacy runner startup does not require probing the Claude Code binary.

Changed files:

- `deploy/code-agent-runner.Dockerfile`
- `apps/api/app/services/code_agent/runner.py`
- `apps/api/tests/test_code_agent_runner.py`

Verification:

- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_runner.py apps/api/tests/test_code_agent_runtime_context.py apps/api/tests/test_code_agent_runner_protocol.py`
  - Result: 67 passed, 3 warnings.

Task evidence:

- 2.1: Dockerfile test confirms the CLI package version is pinned and not installed via `latest`; runner test confirms `claude --version` is recorded in run runner facts for `claude_code` runs alongside existing image/image_id facts.

#### Phase 2 implementation: Claude Code runtime preflight

- Added `apps/api/app/services/code_agent/claude_code_runtime.py` with a run-bound Claude Code preflight contract.
- Preflight checks execute through the existing runner `exec` path, not through API process or host shell.
- Implemented stable preflight failure classifications and reasons for:
  - CLI availability: `runtime_unavailable` / `claude_code_cli_unavailable`
  - run-local config dir writability: `runtime_unavailable` / `claude_code_config_dir_unwritable`
  - repository cwd read/write access: `runtime_unavailable` / `claude_code_repository_cwd_unwritable`
  - MCP config parsing: `mcp_config_failed` / `claude_code_mcp_config_invalid`
  - model reachability probe: `model_unavailable` / `claude_code_model_unavailable`
  - budget watchdog availability: `infrastructure_error` / `claude_code_budget_watchdog_unavailable`
  - exhausted runtime budget: `budget_exhausted` / `claude_code_budget_exhausted`
- Added a default MVP model gate requiring `provider=cloud_claude` and a non-empty `model_ref`; tests can inject a model reachability probe without performing network access.

Changed files:

- `apps/api/app/services/code_agent/claude_code_runtime.py`
- `apps/api/tests/test_code_agent_claude_code_runtime.py`

Verification:

- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_claude_code_runtime.py apps/api/tests/test_code_agent_runner.py`
  - Result: 24 passed, 1 warning.

Task evidence:

- 2.2: Unit tests cover successful preflight and each specified failure point with stable `failure_type` and `reason`.

#### Phase 2 implementation: existing runner/container launch boundary

- Connected Claude Code preflight to the existing CodeAgent run startup path after the existing runner container starts.
- `claude_code` runs now execute runtime preflight through the existing `CodeContainerRunner.exec(container_id, ...)` path.
- Repository cwd for Claude Code preflight is the prepared runner workspace mount (`/workspace`), not an API/host path.
- Preflight results are recorded under `runner_facts.claude_code_preflight`.
- Preflight failure fail-closes the run before the standard CodeAgent runtime proceeds.
- Legacy runs remain unaffected and do not run Claude Code preflight.

Changed files:

- `apps/api/app/services/agent_runtime/runtime.py`
- `apps/api/app/services/code_agent/claude_code_runtime.py`
- `apps/api/tests/test_code_agent_runtime_context.py`

Verification:

- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_runtime_context.py apps/api/tests/test_code_agent_claude_code_runtime.py apps/api/tests/test_code_agent_runner.py`
  - Result: 41 passed, 3 warnings.

Task evidence:

- 2.3: Integration test proves a `claude_code` run uses exactly one existing `CodeContainerRunner.start` call, executes preflight through the existing container id, uses `/workspace` as repository cwd, and does not call host `subprocess.run`.

#### Phase 3 implementation: ClaudeCodeRuntimeAdapter base contract

- Added frozen `ClaudeCodeRuntimeInput` with run id, existing container id, objective, frozen task contract, effective policy, model config, repository cwd, runtime config dir, allowed tools, validation plan and budgets.
- Added `ClaudeCodeRuntimeAdapter.run(input)` that executes Claude Code through the provided existing runner.
- Adapter command uses non-interactive Claude Code print mode (`claude -p`) with structured JSON output and explicit model/max-turns.
- Added `ClaudeCodeRuntimeResult` that returns coding-stage facts only:
  - `status`
  - `exit_code`
  - `summary`
  - `changed_files`
  - `budget_usage`
  - `tool_audit`
  - transcript paths
  - runtime events
  - error classification/summary
- Explicitly verified adapter output does not include or emit `patch_ready`.

Changed files:

- `apps/api/app/services/code_agent/claude_code_runtime.py`
- `apps/api/tests/test_code_agent_claude_code_runtime.py`

Verification:

- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_claude_code_runtime.py`
  - Result: 12 passed.

Task evidence:

- 3.1: Adapter input immutability, runner-based execution and non-`patch_ready` coding-stage result semantics are covered by unit tests.

#### Phase 3 implementation: runtime fact capture and safe persistence

- Extended `ClaudeCodeRuntimeAdapter.run(input)` to capture:
  - exit code
  - summary
  - changed files
  - budget usage
  - tool audit
  - transcript path
  - redacted transcript path
  - runtime events
- Added recursive redaction before runtime output is exposed through adapter results or persisted to run fields.
- Added `record_claude_code_runtime_result(run, result)` to persist coding-stage facts into:
  - `runner_facts.claude_code_runtime`
  - `budget_usage.claude_code_runtime`
  - `tool_audit`
- Verified secret-like content is not stored in `runner_facts`, `budget_usage` or `tool_audit`.

Changed files:

- `apps/api/app/services/code_agent/claude_code_runtime.py`
- `apps/api/tests/test_code_agent_claude_code_runtime.py`

Verification:

- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_claude_code_runtime.py`
  - Result: 13 passed.

Task evidence:

- 3.2: Unit test verifies exit/summary/files/budget/tool audit/transcript paths are captured and persisted without exposing a secret-like value.

#### Phase 3 implementation: same-run session reuse boundary

- Added deterministic Claude Code session names derived from CodeAgent `run_id`.
- Initial Claude Code adapter invocation uses `--name code-agent-run-<run_id>`.
- Retry invocation uses `--resume code-agent-run-<run_id>`.
- The runtime input ignores caller-provided session names and derives the session name from the frozen run id.
- Verifier feedback included in retry prompts is redacted before command construction.
- Different CodeAgent runs derive different Claude Code session names.

Changed files:

- `apps/api/app/services/code_agent/claude_code_runtime.py`
- `apps/api/tests/test_code_agent_claude_code_runtime.py`

Verification:

- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_claude_code_runtime.py`
  - Result: 14 passed.

Task evidence:

- 3.3: Unit test verifies initial run naming, same-run retry resume and different-run session isolation.

#### Phase 3 implementation: runtime failure classifications

- Added Claude Code runtime failure classifications:
  - `runtime_unavailable`
  - `model_unavailable`
  - `mcp_config_failed`
  - `skill_load_failed`
  - `budget_exhausted`
  - `coding_failed`
  - `coding_timeout`
  - `verification_failed`
  - `infrastructure_error`
- Added detailed Claude Code failure reason aliases into existing external failure presentation.
- Added result presentation labels for Claude Code runtime/model/MCP/skill/coding timeout and failure statuses.
- Adapter non-zero exit now maps to stable coding-stage failure status, including timeout classification for timeout-shaped exits/output.

Changed files:

- `apps/api/app/services/code_agent/claude_code_runtime.py`
- `apps/api/app/services/code_agent/failures.py`
- `apps/api/app/services/code_agent/results.py`
- `apps/api/tests/test_code_agent_claude_code_runtime.py`

Verification:

- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_claude_code_runtime.py apps/api/tests/test_code_agent_failures.py`
  - Result: 56 passed, 2 warnings.

Task evidence:

- 3.4: Unit tests verify stable classification for every required Claude Code failure class and verify each is presentable through existing failure/result presentation structures.

Related finding:

- A broader run including `apps/api/tests/test_code_agent_results.py` exposed an unrelated artifact review failure (`reviewed["data"] is None`). Recorded in `findings.md`; not used as 3.4 evidence.

#### Phase 4 implementation: run-local Skill markdown materialization

- Added run-local Claude Code Skill materialization under `<workspace>/.claude/skills/<skill>/SKILL.md`.
- Skill selection is driven only by the frozen run contract `allowed_skills`.
- Unbound/unauthorized skills are not materialized.
- Added audit-visible skill facts under `runner_facts.claude_code_skills`:
  - skill id
  - skill name
  - slug
  - source
  - version
  - content hash
  - run-local path
- Added `skill_loaded` runtime events with metadata only; Skill markdown content is not copied into runner facts.
- Integrated Skill materialization into the `claude_code` run startup path before runtime preflight.

Changed files:

- `apps/api/app/services/code_agent/claude_code_runtime.py`
- `apps/api/app/services/agent_runtime/runtime.py`
- `apps/api/tests/test_code_agent_claude_code_runtime.py`
- `apps/api/tests/test_code_agent_runtime_context.py`

Verification:

- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_claude_code_runtime.py apps/api/tests/test_code_agent_runtime_context.py`
  - Result: 42 passed, 3 warnings.

Task evidence:

- 4.1: Unit and integration tests verify only frozen authorized skills are written to `.claude/skills/<skill>/SKILL.md`, and skill name/source/version/hash are visible in runner facts/events.

#### Phase 4 implementation: authorized Skill resource/script copying

- Extended run-local Skill materialization to copy non-`SKILL.md` files from the authorized Skill package into the matching `.claude/skills/<skill>/` directory.
- Copies nested resource/script files while preserving relative paths.
- Skips symlinks and any path that cannot be proven to remain under the source Skill root and target run-local Skill directory.
- Keeps unbound/unauthorized Skill resources out of the run-local Claude Code skill directory.
- Records `resource_count` in `skill_loaded` events.

Changed files:

- `apps/api/app/services/code_agent/claude_code_runtime.py`
- `apps/api/tests/test_code_agent_claude_code_runtime.py`

Verification:

- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_claude_code_runtime.py apps/api/tests/test_code_agent_runtime_context.py`
  - Result: 43 passed, 3 warnings.

Task evidence:

- 4.2: Unit test verifies authorized scripts/resources are copied, unbound Skill directory is not created, and symlink escape is not copied.

#### Phase 4 implementation: run-local MCP config

- Added run-local Claude Code MCP config generation at `<workspace>/.claude/mcp.json`.
- MCP selection is driven only by the frozen run contract `authorized_mcp_servers`.
- Supports command-based MCP config and URL/protocol config shape for currently authorized MCP rows.
- Does not include `command_env` or headers in the generated config at this stage, avoiding plaintext secret persistence.
- Added audit-visible MCP facts under `runner_facts.claude_code_mcp`:
  - MCP id
  - MCP name
  - authorization state
  - enabled tool count
  - config path
- Added `mcp_loaded` runtime events with metadata only.
- Integrated MCP config materialization into the `claude_code` startup path and passed the generated config JSON into preflight parsing.

Changed files:

- `apps/api/app/services/code_agent/claude_code_runtime.py`
- `apps/api/app/services/agent_runtime/runtime.py`
- `apps/api/tests/test_code_agent_claude_code_runtime.py`

Verification:

- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_claude_code_runtime.py apps/api/tests/test_code_agent_runtime_context.py`
  - Result: 44 passed, 3 warnings.

Task evidence:

- 4.3: Unit test verifies only frozen authorized MCP is written to `.claude/mcp.json`; unbound MCP is absent; authorization state and enabled tool count are audited and visible in runner facts/events.

#### Phase 4 implementation: credential-safe MCP/model config

- MCP config now only allows explicit env references (`${ENV_NAME}` / `$ENV_NAME`) from `command_env`.
- Plaintext MCP env values are omitted from generated config.
- MCP headers are not written into Claude Code config, preventing bearer/token leakage.
- Claude Code command construction uses `model_ref` only; model credentials in `model_config` are not included in the command.

Changed files:

- `apps/api/app/services/code_agent/claude_code_runtime.py`
- `apps/api/tests/test_code_agent_claude_code_runtime.py`

Verification:

- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_claude_code_runtime.py`
  - Result: 29 passed, 1 warning.

Task evidence:

- 4.4: Unit tests verify env references are preserved, plaintext secret-like values and headers are not persisted in generated MCP config, and model credential fields do not enter the Claude command line.

#### Phase 5 implementation: runtime event normalization and startup publication

- Added `normalize_claude_code_runtime_event(...)` for stable CodeAgent profile payloads.
- Supported required event phases:
  - `runtime_started`
  - `skill_loaded`
  - `mcp_loaded`
  - `tool_call`
  - `file_changed`
  - `test_run`
  - `verifier_failed_retrying`
  - `verifier_passed`
  - `artifact_sealed`
- Runtime event payloads use existing CodeAgent profile envelope with `version=1`, `profile=code`, `phase`, `status` and `run_id`.
- Added redaction for event summary/tool/path metadata before publication.
- Published startup `runtime_started`, `skill_loaded` and `mcp_loaded` events during `claude_code` run startup via the existing hub.

Changed files:

- `apps/api/app/services/code_agent/claude_code_runtime.py`
- `apps/api/app/services/agent_runtime/runtime.py`
- `apps/api/tests/test_code_agent_claude_code_runtime.py`
- `apps/api/tests/test_code_agent_runtime_context.py`

Verification:

- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_claude_code_runtime.py apps/api/tests/test_code_agent_runtime_context.py`
  - Result: 55 passed, 3 warnings.

Task evidence:

- 5.1: Unit tests verify all required runtime event types normalize to stable frontend-safe profile payloads; integration test verifies `runtime_started`, `skill_loaded` and `mcp_loaded` reach the existing frontend event hub for a `claude_code` run.

#### Phase 5 implementation: output and transcript redaction

- Added transcript writer that stores raw and redacted Claude Code transcript files separately under `.claude/transcripts`.
- Added transcript redaction with the existing CodeAgent `redact_code_output` path.
- Runtime event normalization and runtime result persistence already redact values before user-visible display or audit persistence.
- Existing source/patch/artifact scanning remains independent; this change only controls output/transcript visibility.

Changed files:

- `apps/api/app/services/code_agent/claude_code_runtime.py`
- `apps/api/tests/test_code_agent_claude_code_runtime.py`

Verification:

- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_claude_code_runtime.py apps/api/tests/test_code_agent_tools.py apps/api/tests/test_code_agent_security.py`
  - Result: 55 passed, 1 warning.

Task evidence:

- 5.2: Unit test verifies redacted transcript copy removes secret-like content; runtime result/event tests verify persisted/user-visible payloads are redacted while existing scanner tests continue passing.

#### Phase 5 implementation: runtime-only file exclusion from canonical diff

- Added `.claude/` runtime-only path recognition in Workspace change tracking.
- Excluded `.claude/` files from `workspace_changed_paths`.
- Updated Sealer canonical diff generation to compare against a temporary canonical workspace copy that excludes runtime-only `.claude/` files.
- Updated diff header normalization for the canonical workspace copy.
- Verified runtime-only MCP config, Skill files and transcript-like files do not enter sealed patch.

Changed files:

- `apps/api/app/services/code_agent/workspace.py`
- `apps/api/app/services/code_agent/artifacts.py`
- `apps/api/tests/test_code_agent_artifacts.py`

Verification:

- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_artifacts.py apps/api/tests/test_code_agent_verifier.py apps/api/tests/test_code_agent_claude_code_runtime.py`
  - Result: 79 passed, 50 warnings.

Task evidence:

- 5.3: Artifact test verifies `.claude` runtime files are absent from changed paths and sealed `patch.diff`, while real business source changes remain in the canonical patch.

#### Phase 6 implementation: Claude Code coding_completed enters authoritative Verifier

- Connected `claude_code` CodeAgent runs from `_run_code_runtime(...)` to `ClaudeCodeRuntimeAdapter.run(...)`.
- Kept legacy CodeAgent runtime path unchanged for non-`claude_code` runs.
- For `claude_code` runs, adapter `coding_completed` summary becomes the coding-stage result only.
- Existing `CodeVerifier` still runs after `coding_completed`.
- Sealer is not invoked when Verifier fails.
- Claude Code success exit/done text does not mark a run `patch_ready`.

Changed files:

- `apps/api/app/services/agent_runtime/runtime.py`
- `apps/api/app/services/code_agent/claude_code_runtime.py`
- `apps/api/tests/test_code_agent_runtime_context.py`

Verification:

- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_runtime_context.py apps/api/tests/test_code_agent_claude_code_runtime.py apps/api/tests/test_code_agent_verifier.py apps/api/tests/test_code_agent_artifacts.py`
  - Result: 97 passed, 52 warnings.

Task evidence:

- 6.1: Regression test verifies a `claude_code` adapter result with `status=coding_completed`, `exit_code=0` and `summary=done` still enters the existing Verifier; Verifier failure sets run status to `verification_failed`, does not call Sealer and never produces `patch_ready`.

#### Phase 6 implementation: Verifier failure same-session retry

- Added redacted Verifier feedback generation for Claude Code repair retries.
- Extended runtime input construction with `retry_attempt` and `verifier_feedback`.
- Added frozen `runtime_budgets.max_verifier_retries` lookup with a hard upper bound of 2.
- Updated `claude_code` runtime orchestration so Verifier failure triggers another `ClaudeCodeRuntimeAdapter.run(...)` call when retry budget remains.
- Retry uses the same run-derived Claude Code session, same `/workspace` repository cwd, same container id and same frozen effective policy.
- Published `verifier_failed_retrying` events with retry attempt and max retry metadata.
- Verified secret-like Verifier output is redacted before being sent to Claude Code or exposed in retry events.

Changed files:

- `apps/api/app/services/agent_runtime/runtime.py`
- `apps/api/app/services/code_agent/claude_code_runtime.py`
- `apps/api/tests/test_code_agent_runtime_context.py`

Verification:

- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_runtime_context.py apps/api/tests/test_code_agent_claude_code_runtime.py apps/api/tests/test_code_agent_verifier.py apps/api/tests/test_code_agent_artifacts.py`
  - Result: 98 passed, 52 warnings.

Task evidence:

- 6.2: Regression test verifies initial Verifier failure causes a retry in the same Claude Code session using the same Workspace/container/frozen policy; the retry receives redacted Verifier feedback and emits a redacted `verifier_failed_retrying` event before succeeding through Verifier and Sealer.

#### Phase 6 implementation: verifier retry limits and budget fail-close

- Enforced `runtime_budgets.max_verifier_retries` with default 2 and hard upper bound 2.
- Verified retry exhaustion stops after initial coding attempt plus at most two verifier repair retries.
- Verified retry exhaustion produces stable `verification_failed` status and does not invoke Sealer.
- Changed `budget_exhausted` Verifier outcome to fail closed immediately without launching another Claude Code repair retry.

Changed files:

- `apps/api/app/services/agent_runtime/runtime.py`
- `apps/api/app/services/code_agent/claude_code_runtime.py`
- `apps/api/tests/test_code_agent_runtime_context.py`

Verification:

- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_runtime_context.py apps/api/tests/test_code_agent_claude_code_runtime.py apps/api/tests/test_code_agent_verifier.py apps/api/tests/test_code_agent_artifacts.py`
  - Result: 100 passed, 52 warnings.

Task evidence:

- 6.3: Regression tests verify an inflated `max_verifier_retries=99` is capped to retry attempts `[0, 1, 2]` and ends as `verification_failed`; a `budget_exhausted` Verifier result performs no retry and ends as `budget_exhausted`.

#### Phase 6 implementation: Sealer-owned patch_ready transition

- Preserved existing Sealer ownership for canonical diff and sealed artifact generation after Claude Code coding completion.
- Added explicit Claude Code runtime events for `verifier_passed` and `artifact_sealed`.
- Verified `patch_ready` is produced only when:
  - Claude Code returns `coding_completed`
  - existing Verifier passes
  - existing Sealer succeeds
- Verified Sealer failure after Verifier pass remains a stable non-success status and does not produce `patch_ready`.

Changed files:

- `apps/api/app/services/agent_runtime/runtime.py`
- `apps/api/tests/test_code_agent_runtime_context.py`

Verification:

- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_runtime_context.py apps/api/tests/test_code_agent_claude_code_runtime.py apps/api/tests/test_code_agent_verifier.py apps/api/tests/test_code_agent_artifacts.py`
  - Result: 102 passed, 52 warnings.

Task evidence:

- 6.4: Regression tests verify Verifier pass plus Sealer success transitions a Claude Code run to `patch_ready` and emits `verifier_passed`/`artifact_sealed`; Sealer failure after Verifier pass transitions to `infrastructure_error` and never marks `patch_ready`.

#### Phase 7 implementation: minimal Coding Runtime selection UI

- Added API body/response/options support for Manifest-level `coding_runtime`.
- Exposed approved `coding_runtimes` through Manifest options.
- Stored Manifest `coding_runtime` in the existing policy JSON, avoiding a new DB column or separate runtime settings model.
- Added Code Projects Manifest form select for `legacy` vs `claude_code`.
- Kept Agents editor scoped to Profile and Code Project selection; Standard Agent configuration does not receive a runtime field.
- Preserved legacy default when runtime is omitted.

Changed files:

- `apps/api/app/routers/code_project.py`
- `apps/web/src/views/CodeProjects.vue`
- `apps/api/tests/test_code_agent_control_plane_ui.py`

Verification:

- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_control_plane_ui.py apps/api/tests/test_code_agent_policy_layers.py apps/api/tests/test_code_agent_control_plane.py apps/api/tests/test_code_agent_runtime_context.py`
  - Result: 127 passed, 126 warnings.

Task evidence:

- 7.1: API/UI contract tests verify Manifest options expose `["legacy", "claude_code"]`, saving a draft with `coding_runtime="claude_code"` persists it into policy and response, the CodeProjects component renders a runtime selector, and the Agents editor does not add a Standard Agent runtime field.

#### Phase 7 implementation: runtime process visibility in CodeAgent UI

- Added CodeAgent result `runtime` summary payload with:
  - selected `coding_runtime`
  - Claude Code preflight facts
  - loaded Skill metadata
  - loaded MCP metadata
  - latest Claude Code runtime result facts
- Added AgentChat result banner display for runtime type, preflight status, Skill count and MCP count.
- Added expandable Claude Code Runtime evidence section.
- Extended CodeAgent profile event labels for:
  - `runtime_started`
  - `skill_loaded`
  - `mcp_loaded`
  - `tool_call`
  - `file_changed`
  - `test_run`
  - `verifier_failed_retrying`
  - `verifier_passed`
  - `artifact_sealed`
- Event-to-step content now surfaces redacted summary, Skill/MCP name, command, path or artifact id where present.

Changed files:

- `apps/api/app/services/code_agent/results.py`
- `apps/web/src/views/AgentChat.vue`
- `apps/api/tests/test_code_agent_results.py`
- `apps/api/tests/test_code_agent_control_plane_ui.py`

Verification:

- Broad check attempted:
  - `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_results.py apps/api/tests/test_code_agent_control_plane_ui.py apps/api/tests/test_code_agent_runtime_context.py apps/api/tests/test_code_agent_claude_code_runtime.py`
  - Result: 1 unrelated known failure in `test_project_authorized_review_and_fixed_file_download_interfaces`; 106 passed before failure report.
- Targeted 7.2 verification:
  - `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_results.py::test_code_result_includes_claude_code_runtime_visibility_facts apps/api/tests/test_code_agent_control_plane_ui.py::test_agent_chat_shows_code_context_verifier_evidence_and_only_reviews_patch_ready apps/api/tests/test_code_agent_runtime_context.py::test_claude_code_verifier_failure_retries_same_session_with_redacted_feedback apps/api/tests/test_code_agent_runtime_context.py::test_claude_code_patch_ready_requires_verifier_pass_and_sealer_success apps/api/tests/test_code_agent_claude_code_runtime.py`
  - Result: 43 passed, 1 warning.

Task evidence:

- 7.2: Serializer test verifies `dbt-clickhouse-gamestat` Skill metadata and MCP/preflight/runtime facts are exposed in result payload; AgentChat component test verifies runtime type, preflight, Skill/MCP counts, runtime evidence panel and all required process event labels are visible.

#### Phase 7 implementation: actionable failure reason display and runtime evidence redaction

- Added serializer-level recursive redaction for CodeAgent runtime evidence before API/UI exposure.
- Added AgentChat result banner display for stable failure reason, stage and actionable detail.
- Kept raw Claude Code output out of the UI contract; user-visible runtime evidence uses sanitized result facts.

Changed files:

- `apps/api/app/services/code_agent/results.py`
- `apps/web/src/views/AgentChat.vue`
- `apps/api/tests/test_code_agent_results.py`
- `apps/api/tests/test_code_agent_control_plane_ui.py`

Verification:

- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_results.py::test_code_result_includes_claude_code_runtime_visibility_facts apps/api/tests/test_code_agent_control_plane_ui.py::test_agent_chat_shows_code_context_verifier_evidence_and_only_reviews_patch_ready apps/api/tests/test_code_agent_failures.py apps/api/tests/test_code_agent_claude_code_runtime.py`
  - Result: 73 passed, 2 warnings.

Task evidence:

- 7.3: Result serializer test verifies secret-like runtime evidence is redacted before exposure; AgentChat component test verifies stable `failure.reason`, `failure.stage`, `failure.detail` display and no `raw_output` binding.

#### Phase 8 validation: backend unit coverage

- Re-ran the targeted backend unit suite covering:
  - runtime selection and legacy rollback
  - policy/run contract freeze
  - adapter input/output contract
  - stable failure classification
  - runtime/result redaction
  - verifier/sealer gating behavior

Verification:

- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_policy_layers.py apps/api/tests/test_code_agent_control_plane.py apps/api/tests/test_code_agent_claude_code_runtime.py apps/api/tests/test_code_agent_failures.py apps/api/tests/test_code_agent_runtime_context.py apps/api/tests/test_code_agent_results.py::test_required_terminal_states_have_stable_presentation_and_only_verified_seal_is_adoptable apps/api/tests/test_code_agent_results.py::test_code_result_includes_claude_code_runtime_visibility_facts apps/api/tests/test_code_agent_results.py::test_code_result_api_is_session_scoped_project_authorized_and_marks_unverified_result`
  - Result: 160 passed, 36 warnings.

Task evidence:

- 8.1: Targeted backend unit coverage exists and passes for runtime selection, policy freeze, adapter contract, failure classification, redaction and rollback behavior.

#### Phase 8 validation: sandbox/harness integration coverage

- Re-ran controlled integration/harness tests proving:
  - Claude Code preflight and adapter execution use the existing CodeAgent runner container.
  - Repository cwd is `/workspace`.
  - Skill and MCP configuration is run-local and loaded before preflight.
  - Retry continues in the same Workspace/container/frozen policy boundary.
  - Retry budget is enforced.
  - No host/API process fallback occurs.

Verification:

- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_runtime_context.py::test_claude_code_preflight_uses_existing_runner_container_and_workspace apps/api/tests/test_code_agent_runtime_context.py::test_claude_code_verifier_failure_retries_same_session_with_redacted_feedback apps/api/tests/test_code_agent_runtime_context.py::test_claude_code_verifier_retry_exhaustion_stops_after_initial_plus_two apps/api/tests/test_code_agent_claude_code_runtime.py apps/api/tests/test_code_agent_runner.py`
  - Result: 57 passed, 1 warning.

Task evidence:

- 8.2: Controlled harness tests verify Claude Code stays inside the existing sandbox/runner, uses `/workspace`, loads run-local Skill/MCP config, respects retry budgets and has no host/API execution fallback.

#### Phase 8 validation: verifier retry and patch_ready gating coverage

- Re-ran Verifier retry focused tests covering:
  - initial Verifier failure after Claude Code coding completion
  - same-session repair retry
  - retry success through Verifier and Sealer
  - retry exhaustion
  - budget exhaustion without retry
  - Sealer failure after Verifier pass
  - no `patch_ready` without both Verifier pass and Sealer success

Verification:

- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_runtime_context.py::test_claude_code_completed_cannot_bypass_failed_authoritative_verifier apps/api/tests/test_code_agent_runtime_context.py::test_claude_code_verifier_failure_retries_same_session_with_redacted_feedback apps/api/tests/test_code_agent_runtime_context.py::test_claude_code_verifier_retry_exhaustion_stops_after_initial_plus_two apps/api/tests/test_code_agent_runtime_context.py::test_claude_code_budget_exhausted_verifier_result_does_not_retry apps/api/tests/test_code_agent_runtime_context.py::test_claude_code_patch_ready_requires_verifier_pass_and_sealer_success apps/api/tests/test_code_agent_runtime_context.py::test_claude_code_sealer_failure_after_verifier_pass_does_not_patch_ready`
  - Result: 6 passed, 1 warning.

Task evidence:

- 8.3: Focused tests verify initial failure, same-session repair, retry success, retry exhaustion, budget exhaustion and the invariant that only existing Verifier plus existing Sealer can produce `patch_ready`.

#### Phase 8 validation: UI runtime selection and event rendering coverage

- Re-ran UI contract tests covering:
  - Manifest runtime selection
  - Standard Agent editor compatibility
  - runtime/preflight/Skill/MCP visibility
  - runtime event labels
  - verifier retry status
  - artifact status

Verification:

- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_control_plane_ui.py::test_manifest_component_limits_security_fields_to_server_options_and_references apps/api/tests/test_code_agent_control_plane_ui.py::test_agent_editor_component_submits_profile_and_requires_ready_project apps/api/tests/test_code_agent_control_plane_ui.py::test_agent_chat_shows_code_context_verifier_evidence_and_only_reviews_patch_ready apps/api/tests/test_code_agent_control_plane_ui.py::test_structured_manifest_draft_saves_canonical_approved_source_and_reference apps/api/tests/test_code_agent_control_plane_ui.py::test_manifest_options_expose_only_approved_values_and_reference_metadata`
  - Result: 5 passed, 9 warnings.

Task evidence:

- 8.4: UI contract tests verify runtime selection and runtime event/result rendering, including Skill/MCP visibility and verifier retry/artifact status labels.

#### Phase 8 validation: MVP acceptance scenarios

- Added controlled harness MVP acceptance coverage for:
  - single-file change
  - multi-file change
  - test-fail-then-fix
  - bound `dbt-clickhouse-gamestat` Skill repository modification
- Each successful scenario reaches `patch_ready` only through Claude Code `coding_completed` → existing Verifier pass → existing Sealer success.
- The bound dbt Skill scenario verifies the dbt Skill loading evidence is recorded in runner facts.

Verification:

- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_runtime_context.py::test_claude_code_mvp_acceptance_scenarios_reach_patch_ready_through_verifier_and_sealer apps/api/tests/test_code_agent_runtime_context.py::test_claude_code_verifier_failure_retries_same_session_with_redacted_feedback`
  - Result: 5 passed, 1 warning.

Task evidence:

- 8.5: Controlled MVP harness tests verify single-file, multi-file, test-fail-then-fix and bound dbt Skill modification scenarios all reach `patch_ready` through existing Verifier/Sealer gates.

#### Phase 8 validation: final OpenSpec/backend/frontend checks

- Ran final OpenSpec validation.
- Ran relevant backend target suite covering implemented backend and UI contract changes.
- Ran frontend production build.

Verification:

- `openspec validate code-agent-claude-code-runtime --strict`
  - Result: `Change 'code-agent-claude-code-runtime' is valid`.
- `PYTHONPATH=apps/api pytest -q apps/api/tests/test_code_agent_policy_layers.py apps/api/tests/test_code_agent_control_plane.py apps/api/tests/test_code_agent_control_plane_ui.py apps/api/tests/test_code_agent_runtime_context.py apps/api/tests/test_code_agent_claude_code_runtime.py apps/api/tests/test_code_agent_runner.py apps/api/tests/test_code_agent_verifier.py apps/api/tests/test_code_agent_artifacts.py apps/api/tests/test_code_agent_failures.py apps/api/tests/test_code_agent_results.py::test_required_terminal_states_have_stable_presentation_and_only_verified_seal_is_adoptable apps/api/tests/test_code_agent_results.py::test_code_result_includes_claude_code_runtime_visibility_facts apps/api/tests/test_code_agent_results.py::test_code_result_api_is_session_scoped_project_authorized_and_marks_unverified_result`
  - Result: 260 passed, 176 warnings.
- `npm run build` from `apps/web`
  - Result: build succeeded.
  - Warnings: Rollup removed unsupported pure annotations from `@vueuse/core`; chunk size warnings for large bundles.

Task evidence:

- 8.6: OpenSpec strict validation, relevant backend tests and frontend build completed successfully; exact commands and results are recorded above.
