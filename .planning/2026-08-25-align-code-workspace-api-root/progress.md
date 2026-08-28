# Progress Log: align-code-workspace-api-root（Implementation）

## Session: 2026-08-25

### Current Status

- **Tracking state:** apply complete
- **Current execution phase:** Phase 3 complete
- **OpenSpec completion:** 5 / 5 tasks
- **Implementation status:** WorkspaceManager root aligned; orphan deleted; API restarted; prepare+mount smoke OK
- **Archive:** not yet (ready for `/opsx:archive` after optional UI confirm)
- **Follow-up (user chose reload fix #1):** `scripts/start-local.sh` now starts uvicorn with cwd=`apps/api/app` so WatchFiles does not watch `data/code-agent/**`. Live: watch dirs `['.../apps/api/app']`; `/health` ok. Web restarted on `:5173`. Re-run CodeAgent init without restarting API mid-run.

### Initialization Actions

- Read OpenSpec change artifacts and initialized Planning with Files (see earlier session notes).
- Formal tasks remained unchecked until code+verification evidence landed.

### Apply Actions

#### Task 1.1

- Changed `WorkspaceManager.__init__` to prefer `settings.code_workspace_api_root`, else `{data_dir}/code-agent/runs`.
- File: `apps/api/app/services/code_agent/workspace.py`
- Verification: `pytest -q tests/test_code_agent_workspace.py::test_workspace_manager_default_root_prefers_configured_api_root tests/test_code_agent_workspace.py::test_workspace_manager_default_root_falls_back_to_hyphenated_path` → passed

#### Task 1.2

- Added `test_prepare_materializes_workspace_under_configured_api_root` in `tests/test_code_agent_workspace.py`.
- Verification: that test → passed (with the two root unit tests: 3 passed)

#### Task 2.1

- Verification: `pytest -q tests/test_code_agent_workspace_mount.py tests/test_code_agent_runner.py` → **23 passed**

#### Task 3.1

- `chmod -R u+w` then deleted `apps/api/data/code_agent/runs` (and empty parent).
- Verification: path no longer exists.

#### Task 3.2

- Restarted API via direct uvicorn on `:8000` after `start-local.sh` left a stale pid / empty log during cloudflared-enabled attempts.
- `/health` → `{"status":"ok",...}`
- Live settings: `WorkspaceManager().root` == configured `code_workspace_api_root` (hyphen path); underscore not used.
- Live smoke: prepare under API root + `resolve_workspace_host_path` → **SMOKE_OK** (`host_mount_ok`, under host root).
- Outside-root resolve still raises `workspace_mount_invalid`.
- Note: full browser CodeAgent UI init / Docker runner image readiness was not re-driven in UI; the specific root-skew `workspace_mount_invalid` failure mode is fixed by code+smoke. If Sandbox still fails in UI, check separate readiness (e.g. trusted image digest / local root seed skip), not path skew.

### OpenSpec Task Completion Ledger

| Task | Code / deliverable | Verification evidence | Progress recorded | `tasks.md` checked | Status |
|---|---|---|---|---|---|
| 1.1 | `workspace.py` default root | 2 unit tests passed | yes | yes | complete |
| 1.2 | prepare regression test | 1 test passed | yes | yes | complete |
| 2.1 | (no code change) | mount+runner 23 passed | yes | yes | complete |
| 3.1 | deleted orphan dir | path absent | yes | yes | complete |
| 3.2 | API restart + smoke | health OK; prepare+mount SMOKE_OK | yes | yes | complete |
