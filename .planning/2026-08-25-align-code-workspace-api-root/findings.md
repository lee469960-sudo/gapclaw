# Findings: align-code-workspace-api-root

## Pre-implementation (from grill + proposal)

- UI symptom: CodeAgent init —「准备 Code Workspace」成功，「启动 Code Sandbox」失败，稳定原因 `workspace_mount_invalid`。
- Disk evidence: workspaces land under `apps/api/data/code_agent/runs/` (underscore); configured dual roots point at `.../data/code-agent/runs` (hyphen); latter empty of runs.
- Code evidence: `WorkspaceManager` default root uses `{data_dir}/code_agent/runs` and ignores `code_workspace_api_root` unless `root=` is passed; mount resolver requires `<api_root>/<run_id>/workspace`.
- Formal scope locked in OpenSpec change `align-code-workspace-api-root` (proposal / design / specs / tasks).

## During execution

- Orphan runs under `code_agent/runs` were mode `read-only` from retention; delete required `chmod -R u+w` before `rmtree`.
- `scripts/start-local.sh` with cloudflared left stale `api.pid` / empty `api.log` in this session; direct `uvicorn` on `:8000` restored `/health`.
- Startup log note `seed code-agent skip: missing local root or trusted digest` is orthogonal to mount-root skew; track separately if UI Sandbox still fails after path fix.
- After path fix, runs `1fbadf24` / `0e65322b` failed as `infrastructure_error` / `startup_recovery`: WatchFiles saw Workspace `.py` under `data/code-agent/runs` and reloaded uvicorn; janitor fail-closed pending runs. `--reload-exclude` and dropping `--reload-include '*.py'` were not enough — uvicorn WatchFilesReload always appends `Path.cwd()` and FileFilter default-includes `*.py`. Fix: start API with cwd=`apps/api/app` plus `--app-dir` / `--env-file`.

## Open questions

None — grilled decisions cover scope; OpenSpec design Open Questions is empty.
