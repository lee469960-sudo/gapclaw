## 1. Runtime selection and policy freeze

- [x] 1.1 Add `coding_runtime` support with legacy default and `claude_code` opt-in, and verify existing CodeAgent runs without the flag still use the current runtime
- [x] 1.2 Freeze `coding_runtime`, model config, runtime budgets, allowed skills and authorized MCP servers in each CodeAgent run contract, and verify the frozen policy cannot be expanded during runtime execution
- [x] 1.3 Add feature flag / kill switch handling for `claude_code`, and verify disabling it causes new runs to fall back to legacy runtime without deleting historical run evidence

## 2. Trusted runner image and preflight

- [x] 2.1 Update the trusted runner image build to include a pinned Claude Code CLI version, and verify the image digest and `claude --version` are recorded
- [x] 2.2 Implement Claude Code runtime preflight checks for CLI availability, run-local config dir writability, repository cwd read/write access, MCP config parsing, model reachability and budget watchdog availability, and verify each failure maps to a stable reason
- [x] 2.3 Ensure Claude Code starts only inside the existing run dedicated runner container with repository cwd set to the prepared Workspace, and verify it never runs in the API process, host process or a second sandbox

## 3. ClaudeCodeRuntimeAdapter

- [x] 3.1 Implement `ClaudeCodeRuntimeAdapter.run(input)` with frozen input fields and verify it returns coding-stage facts rather than `patch_ready`
- [x] 3.2 Capture Claude Code exit code, summary, changed files, budget usage, tool audit, transcript path and redacted transcript path, and verify these are stored on the run without exposing secrets
- [x] 3.3 Implement same-run Claude Code session reuse for verifier retry and verify a new CodeAgent run never reuses another run's session or runtime config
- [x] 3.4 Add stable runtime failure classifications including `runtime_unavailable`, `model_unavailable`, `mcp_config_failed`, `skill_load_failed`, `budget_exhausted`, `coding_failed`, `coding_timeout`, `verification_failed` and `infrastructure_error`, and verify API/UI responses expose actionable reasons

## 4. Skill and MCP injection

- [x] 4.1 Generate run-local `.claude/skills/<skill>/SKILL.md` entries only for the current Agent's bound/authorized skills, and verify skill name, source and version/hash are audited and visible
- [x] 4.2 Copy allowed skill resources/scripts into the run-local skill directory under existing policy constraints, and verify unbound skills and unauthorized resources are not readable by Claude Code
- [x] 4.3 Generate run-local Claude Code MCP config only for authorized MCP servers, and verify MCP server names, authorization state and enabled tool counts are audited and visible
- [x] 4.4 Inject MCP/model credentials only through sandbox secret/env paths, and verify generated config files do not contain plaintext secrets

## 5. Runtime events, transcript and redaction

- [x] 5.1 Map Claude Code process activity into CodeAgent unified events: `runtime_started`, `skill_loaded`, `mcp_loaded`, `tool_call`, `file_changed`, `test_run`, `verifier_failed_retrying`, `verifier_passed` and `artifact_sealed`, and verify the frontend receives stable event payloads
- [x] 5.2 Implement output/transcript redaction before user-visible display or audit persistence, and verify secret-like content is redacted while source/patch/artifact scanners still run independently
- [x] 5.3 Ensure Claude Code run-local settings, MCP config, skills and transcript do not enter the business repository canonical diff, and verify sealed patches exclude runtime-only files

## 6. Verifier and Sealer integration

- [x] 6.1 Connect Claude Code `coding_completed` to the existing CodeAgent Verifier, and verify Claude Code exit success or "done" text alone never marks a run `patch_ready`
- [x] 6.2 Feed脱敏 Verifier failure reports back into the same Claude Code session when retry budget remains, and verify retry uses the same Workspace, frozen policy and audit boundary
- [x] 6.3 Enforce default verifier retry policy of initial attempt plus at most 2 repair retries, and verify retry exhaustion or budget exhaustion results in stable non-success status
- [x] 6.4 Preserve existing Sealer ownership of canonical diff and sealed artifact generation, and verify only Verifier pass plus Sealer success produces `patch_ready`

## 7. Operator UI

- [x] 7.1 Add minimal UI controls to select `claude_code` runtime for CodeAgent/Profile/Manifest where appropriate, and verify Standard Agent and legacy CodeAgent configuration remain unchanged
- [x] 7.2 Display runtime type, preflight status, skill loading, MCP loading, tool/test/file events, verifier retry and artifact status in the CodeAgent conversation/result UI, and verify dbt skill loading is visible for a bound dbt Agent
- [x] 7.3 Display stable actionable failure reasons for runtime, model, MCP, skill, budget, verification and infrastructure failures, and verify raw Claude Code output is not directly exposed

## 8. MVP validation

- [x] 8.1 Add unit tests for runtime selection, policy freeze, adapter input/output contract, failure classification, redaction and rollback behavior, and verify the targeted backend suite passes
- [x] 8.2 Add integration tests or controlled harness tests proving Claude Code runs in the existing sandbox, uses the prepared repo cwd, loads run-local skill/MCP config and respects budgets, and verify no host/API execution fallback occurs
- [x] 8.3 Add verifier retry tests covering initial failure, same-session repair, retry success, retry exhaustion and budget exhaustion, and verify only Existing Verifier/Sealer can produce `patch_ready`
- [x] 8.4 Add UI tests for runtime selection and runtime event rendering, including skill/mcp loaded visibility and verifier retry status
- [x] 8.5 Run MVP acceptance scenarios: single-file change, multi-file change, test-fail-then-fix, and bound dbt skill repository modification; verify each successful case reaches `patch_ready` through Existing Verifier/Sealer
- [x] 8.6 Run `openspec validate code-agent-claude-code-runtime --strict` and the relevant backend/frontend test commands, and record exact results before marking this change complete
