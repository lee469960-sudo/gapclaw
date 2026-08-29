## 1. CodeAgent Workspace API

- [x] 1.1 Add a read-only CodeAgent Run workspace metadata endpoint and verify it returns the run-bound workspace path, repository, commit, and readiness facts without exposing `/workplace`
- [x] 1.2 Add paginated file-tree and text-preview operations bound to `code_run_id`; verify path containment, Run authorization, depth/size limits, and filtering of `.git/**`, `.claude/**`, secrets, and binaries
- [x] 1.3 Add Git status/changed-files preview for the bound workspace; verify it uses the run workspace and never the platform repository or generic sandbox directory
- [x] 1.4 Return explicit `workspace_not_prepared`, `workspace_expired`, and `workspace_mount_invalid` states; verify no fallback to `/workplace/task/`

## 2. CodeAgent Conversation Progress

- [x] 2.1 Expose persisted CodeAgent runtime events through the existing authorized Run/session API; verify event ordering, pagination, and redacted payloads
- [x] 2.2 Add idempotent incremental event delivery for active CodeAgent runs; verify reconnect/refresh does not duplicate events and preserves already completed steps
- [x] 2.3 Serialize terminal result cards from the existing result serializer; verify patch-ready, verification failure, target-not-found, and needs-user-decision states remain distinct

## 3. Frontend Workspace and Conversation UI

- [x] 3.1 Add a CodeAgent-only left Workspace view bound to the active `code_run_id`; verify Standard Agent continues using `/workplace`
- [x] 3.2 Render repository tree, selected text file, Git metadata, and changed-file list with loading, empty, expired, and permission-error states; verify no generic “仓库未挂载” fallback masks the actual reason
- [x] 3.3 Render CodeAgent runtime events in the conversation in order, including Workspace, Skill, MCP, tool, test, repair, Verifier, and artifact steps; verify reconnect merges events idempotently
- [x] 3.4 Render the final task result in the same conversation, including status, summary, verification outcome, and Patch actions only when directly adoptable; verify non-success terminal states cannot display success actions

## 4. Integration, Security, and Compatibility

- [x] 4.1 Enforce existing Run/project authorization and workspace containment for all new endpoints; verify unauthorized, cross-run, expired, and guessed paths are rejected
- [x] 4.2 Verify runtime-only files and Git metadata remain excluded from previews, changed paths, Verifier inputs, and sealed artifacts
- [x] 4.3 Add frontend/backend regression tests covering concurrent Standard Agent and CodeAgent sessions; verify their workspaces and event streams remain isolated
- [x] 4.4 Add migration/rollback handling for clients without CodeAgent Workspace support; verify existing AgentChat and `/workplace` flows remain functional

## 5. Acceptance and Documentation

- [x] 5.1 Add mock acceptance coverage for repository preview, live progress, reconnect recovery, successful Patch result, and blocked terminal results; verify all boundaries through existing Runner/Verifier/Sealer behavior
- [x] 5.2 Document CodeAgent usage: select a CodeAgent, start a Run, inspect Workspace and progress, then review the final result; verify documentation identifies `/workspace` as the CodeAgent root and `/workplace` as unrelated
- [x] 5.3 Run focused backend/frontend tests and record exact results in Planning with Files before marking implementation complete
- [x] 5.4 Run `openspec validate code-agent-workspace-conversation-ui --strict` and record the result before archive
