# Findings: code-agent-claude-code-runtime

## Initialization findings

- OpenSpec change root: `openspec/changes/code-agent-claude-code-runtime/`.
- Required artifacts exist:
  - `proposal.md`
  - `design.md`
  - `tasks.md`
  - `specs/code-agent-coding-runtime/spec.md`
  - `specs/code-agent-profile/spec.md`
  - `specs/code-agent-project-policy/spec.md`
  - `specs/code-agent-sandbox-runtime/spec.md`
  - `specs/code-agent-verification/spec.md`
  - `specs/code-agent-operator-ui/spec.md`
- `openspec validate code-agent-claude-code-runtime --strict` passes at planning initialization.
- `openspec instructions apply --change code-agent-claude-code-runtime --json` reports 30 OpenSpec tasks, 0 complete, state `ready`.
- User requirement: OpenSpec `tasks.md` is the only formal task source. Planning files must not redefine requirements.

## Key constraints read from artifacts

- Claude Code is a `Coding Runtime`, not a regular CodeAgent tool.
- Existing CodeAgent Task, Repository, Workspace, Sandbox, Manifest, Skill/MCP registry, Verifier, Sealer, run status and result management remain authoritative.
- `claude_code` must be explicitly selected; legacy runtime remains default.
- Claude Code must run inside the existing per-run runner/sandbox, with repository cwd pointing at the prepared Workspace.
- No second sandbox, no API-process execution, no host-process fallback.
- Skill/MCP injection must be run-local and limited to currently authorized/bound capabilities.
- Credentials must be injected via sandbox secret/env paths; generated config must not contain plaintext secrets.
- Claude Code `coding_completed` is not `patch_ready`; Existing Verifier and Sealer remain final gate.
- Verifier failure retries reuse the same Claude Code session within the same run only; default is initial verifier attempt plus up to 2 repair retries.
- MVP official model path is Cloud Claude. Local model/gateway production path is out of scope for this change.
- Runtime process visibility is an acceptance concern: runtime, skill, MCP, tool, file, test, verifier retry and artifact events must be visible through stable CodeAgent events.

## Issues / blockers

- User-level `~/.codex/skills/planning-with-files/scripts/session-catchup.py` was missing. Used repository-local `.codex/skills/planning-with-files/scripts/session-catchup.py` instead.
- During a broad 3.4 check, `apps/api/tests/test_code_agent_results.py::test_project_authorized_review_and_fixed_file_download_interfaces` returned `reviewed["data"] is None`. This failure appears unrelated to the Claude Code runtime taxonomy changes because the targeted runtime/failure tests pass and the touched presentation mapping is not used by that artifact review path. It should be rechecked before final MVP validation.
- During the 7.2 broad result/UI check, the same `apps/api/tests/test_code_agent_results.py::test_project_authorized_review_and_fixed_file_download_interfaces` failure reproduced with `TypeError: 'NoneType' object is not subscriptable` because `reviewed["data"]` was `None`. Targeted 7.2 runtime visibility tests passed; keep this as a pre-final-validation issue unless a later OpenSpec task explicitly covers artifact review repair.
