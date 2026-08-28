## 1. Direct execution entry

- [x] 1.1 Add Code Profile + `claude_code` parsing for `开始执行:<objective>` so non-empty suffix creates a Code run with the suffix as objective and original trigger text in audit/run facts; verify with backend chat tests that `开始执行:请修改 local 配置` creates a run without requiring another confirmation
- [x] 1.2 Reject empty `开始执行:` before run creation; verify no `CodeAgentRun` row, no Workspace preparation and no runner start when objective is empty
- [x] 1.3 Keep `开始执行:` semantics scoped to Code Profile + `claude_code`; verify Standard Agent and legacy CodeAgent behavior remain unchanged

## 2. Claude Code workspace root readiness

- [x] 2.1 Add safe single-top-level snapshot flattening for new `claude_code` runs: when snapshot root has only sanitized `.git` plus one ordinary business directory, materialize that directory's contents directly as `/workspace`; verify with workspace tests using a `gamestat/`-style snapshot
- [x] 2.2 Preserve multi-top-level snapshot layout and fail readiness if a single business repo root cannot be proven; verify multi-directory fixture does not auto-strip and returns a stable workspace readiness reason
- [x] 2.3 Materialize `/workspace/.git` from sanitized snapshot metadata for new `claude_code` runs and verify Git top-level equals `/workspace`; verify `git -C <workspace> rev-parse --show-toplevel` and `git -C <workspace> status` target the run workspace, not the platform parent repo
- [x] 2.4 Confirm runtime-only `.claude/` and `.git/**` remain excluded from changed paths, Verifier inputs and sealed patch after flattening; verify existing artifact/runtime exclusion tests plus a flattened workspace fixture
- [x] 2.5 Do not migrate or mutate historical run workspaces; verify tests prove old run paths are not rewritten by the new preparation logic

## 3. Runtime terminal results

- [x] 3.1 Add public `target_not_found` terminal result and failure/result presentation mapping; verify API/result tests show target, search scope and sanitized summary without producing `patch_ready`
- [x] 3.2 Add public `needs_user_decision` terminal result for multiple plausible local/profile/dev candidates or unsafe ambiguity; verify API/result tests show candidate files and sanitized hit summaries while stopping the runner
- [x] 3.3 Ensure `target_not_found` and `needs_user_decision` do not create sealed patch, explanatory repo files or `host-validate.sh`; verify Sealer/artifact tests reject patch generation for both terminal states
- [x] 3.4 Keep `target_not_found` distinct from `verification_failed`, `infrastructure_error` and `no_change_justified`; verify failure taxonomy tests cover the distinct statuses

## 4. LLM binding guidance and readiness UI

- [ ] 4.1 Surface Claude Code LLM binding repair guidance for `llm_group_not_supported`, `llm_api_key_missing` and `llm_model_missing` in Agent edit/run readiness responses; verify backend control-plane tests return reason and repair action without auto-changing `llm_id`
- [ ] 4.2 Update CodeAgent result/run detail UI to separately show workspace files, Git metadata, repo root, container mount and model preflight statuses; verify UI tests render model failure while still showing passed Skill/workspace steps
- [ ] 4.3 Ensure UI does not label Git metadata missing, repo root mismatch, container mount failure or model preflight failure as the single generic “仓库未挂载”; verify text/fixture tests cover each readiness reason

## 5. Host validation output

- [ ] 5.1 Add final-result support for a copyable `host-validate.sh` text block when a task succeeds and host-side validation is useful; verify it is rendered as output text only
- [ ] 5.2 Ensure `host-validate.sh` is not written to the business repository and is not treated as automatic Verifier evidence; verify artifact and result tests cover no repo file and no verifier dependency

## 6. Acceptance and regression

- [ ] 6.1 Add mock-Claude acceptance tests for single-file IP replacement flow, target-not-found flow and needs-user-decision flow; verify all reach the correct terminal status through the existing Verifier/Sealer boundaries
- [ ] 6.2 Add local acceptance instructions for `dbt-test`: user must bind a single usable LLMResource, restart API, then run `开始执行:<objective>`; verify documentation or progress notes record that real Claude credentials are an operator prerequisite, not a code task
- [ ] 6.3 Run focused backend tests for chat gate, workspace, runtime taxonomy, artifacts, control-plane readiness and result presentation; record exact results before marking tasks complete
- [ ] 6.4 Run `openspec validate code-agent-claude-run-readiness-v2 --strict` and record the result before apply completion
