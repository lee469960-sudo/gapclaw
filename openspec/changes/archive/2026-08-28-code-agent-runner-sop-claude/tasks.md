## 1. SOP assets and runner image

- [x] 1.1 Add `apps/api/app/services/code_agent/sop/` with sandbox-safe SKILL.md for `openspec-propose`, `openspec-apply-change`, `openspec-verify-change`, `openspec-archive-change`, and `planning-with-files` (no `$HOME` hooks, no grill-me), plus `CLAUDE.md` that authorizes propose→planning→apply→implement→verify in one run and forbids in-session archive and in-container grill; verify the five skill dirs and `CLAUDE.md` exist
- [x] 1.2 Update `deploy/code-agent-runner.Dockerfile` to pin `npm install -g @fission-ai/openspec@1.10.0`, `COPY` the SOP tree to `/opt/code-agent/sop`, keep Claude Code `2.1.246`, and verify the Dockerfile contains that pin and COPY (no `@latest`, no grill-me)

## 2. Network exception for claude_code

- [x] 2.1 Allow `RunnerSpec` `network_mode` of `none` or `bridge` only; reject `host` and other values; verify `tests/test_code_agent_runner_protocol.py` (bridge accepted, host rejected, legacy none still valid)
- [x] 2.2 Make `spec_for_run` set `bridge` only when frozen `coding_runtime=claude_code`, else `none`; verify runner unit tests that legacy stays `none` and claude_code uses `bridge`

## 3. Agent LLM injection and SOP materialize

- [x] 3.1 Map Agent LLMResource `model` / `base_url` / decrypted key into Claude Code `--model` and exec env (`ANTHROPIC_API_KEY`, optional `ANTHROPIC_BASE_URL`); fail `model_unavailable` if missing; verify tests that the command has no plaintext key and that missing key classifies `model_unavailable`
- [x] 3.2 Auto-materialize SOP skills and `CLAUDE.md` into the workspace for `claude_code` only (in addition to existing allowed_skills); verify a unit test that claude_code workspaces contain the five skills and `CLAUDE.md`, and legacy does not

## 4. Two-phase grill

- [x] 4.1 When published Manifest + feature flag would use `claude_code` and the user message is not exactly `开始实现`, do not `create_code_run` and run a no-sandbox grill turn; verify `submit_chat` tests: no `CodeAgentRun` row, no runner start
- [x] 4.2 On exact `开始实现`, create the run with prior session objective/grill context and enqueue the existing code path; verify a test that `开始实现` creates a pending run and that `legacy` still creates a run on the first message

## 5. Archive after seal

- [x] 5.1 After successful Sealer outcome, if runtime is `claude_code`, exec `openspec archive` in the workspace and never archive on verifier/sealer failure; verify tests for archive-on-success and no-archive-on-failure (command invocation can be stubbed)

## 6. Regression

- [x] 6.1 Run `pytest -q tests/test_code_agent_runner_protocol.py tests/test_code_agent_runner.py tests/test_code_agent_control_plane.py tests/test_code_agent_control_plane_ui.py` plus new SOP/grill/archive tests and record the result in `.planning/2026-08-26-code-agent-runner-sop-claude/progress.md`

## 7. Claude Code operational closure

- [x] 7.1 Materialize sanitized Git metadata as a real `/workspace/.git/` directory for `claude_code` workspaces, keep `source.git` only as a compatibility pointer to the same metadata, and verify `git -C <workspace> status` works while `.git/**` is excluded from changed paths and sealed patches
- [x] 7.2 Prevent Claude Code / Code Tool shell from creating commits by rejecting `git commit` in shell policy and failing integrity if HEAD or `refs/heads/snapshot` moves; verify tests cover direct helper git commit rejection, shell command rejection, and integrity failure on ref movement
- [x] 7.3 Remove the retired pre-Claude-Code runner digest from local trusted configuration, delete old `code-agent-*` containers using that digest without deleting their host workspaces, and verify new run configuration selects only the Claude-Code-capable digest
- [x] 7.4 Add/adjust deployment guidance for multi-arch `code-agent-runner` buildx publication so production trusted images use registry `image@sha256:<manifest-list-digest>` covering `linux/arm64` and `linux/amd64`; record that actual push requires a target registry

## 8. Claude Code model binding closure

- [x] 8.1 Split `model_unavailable` runtime reasons for missing Agent LLM, unsupported LLM Group, missing/decrypt-failed API key, and missing model; verify `resolve_claude_code_exec_env` tests cover each reason and still injects only env secrets for a valid single LLM
- [x] 8.2 Reject saving a Code Profile Agent bound to a `claude_code` project when its LLM is a group or lacks key/model; verify Agent control-plane tests cover group rejection and valid single LLM acceptance
- [x] 8.3 Surface the specific model binding reason in failure/result UI copy so users do not misdiagnose it as a runner image or workspace problem; verify result/failure mapping tests cover `llm_group_not_supported`
