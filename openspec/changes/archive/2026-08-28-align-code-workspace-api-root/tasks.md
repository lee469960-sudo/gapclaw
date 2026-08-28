## 1. Align Workspace materialization root

- [x] 1.1 Update `WorkspaceManager` default root to prefer configured `code_workspace_api_root`, with fallback `{data_dir}/code-agent/runs`, and verify with a focused unit assertion that configured vs empty-settings roots resolve as specified
- [x] 1.2 Add or extend a regression test proving prepare places `<run_id>/workspace` under the configured API root (not `code_agent/runs`), and verify the test passes

## 2. Mount fail-closed seam

- [x] 2.1 Confirm existing mount/runner tests still fail closed with `workspace_mount_invalid` when the workspace path is outside the configured API root, and verify `pytest -q tests/test_code_agent_workspace_mount.py tests/test_code_agent_runner.py` passes

## 3. Cleanup and local verification

- [x] 3.1 Delete orphan directory `apps/api/data/code_agent/runs` and verify the path no longer exists
- [x] 3.2 Restart the local API process and verify CodeAgent init for an existing project progresses past “启动 Code Sandbox” without `workspace_mount_invalid` (or document the exact remaining blocker if Sandbox fails for a different reason)
