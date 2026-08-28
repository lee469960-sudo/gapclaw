## Context

See `proposal.md` for motivation. Current CodeAgent startup prepares a Workspace successfully, then fails Sandbox start with `workspace_mount_invalid`.

Constraints:
- `resolve_workspace_host_path` already fail-closes unless the workspace sits at `<api_root>/<run_id>/workspace` with a matching run sentinel.
- Deploy / `.env` already treat `CODE_WORKSPACE_API_ROOT` + `CODE_WORKSPACE_HOST_ROOT` as the dual-root contract (`code-agent/runs` naming).
- `WorkspaceManager()` currently defaults to `{data_dir}/code_agent/runs` and ignores the configured API root unless an explicit `root=` is passed.
- Grilled decisions: align manager to API root; empty-config fallback `{data_dir}/code-agent/runs`; delete orphan `data/code_agent/runs`; restart local API after fix.

## Goals / Non-Goals

**Goals:**
- Single authoritative Workspace API root shared by prepare and mount mapping.
- Preserve fail-closed mount semantics; do not weaken sentinel or containment checks.
- Small, surgical code + test change with clear local cleanup.

**Non-Goals:**
- Redesigning dual-root mapping, sentinel format, or runner isolation.
- Migrating historical orphan workspaces (delete only).
- Changing compose field names or host-root derivation rules.

## Decisions

### 1. `WorkspaceManager` default root = configured API root

When `root` is not passed, resolve in order:
1. `settings.code_workspace_api_root` if non-empty (absolute path as configured)
2. else `{settings.data_dir}/code-agent/runs`

Rationale: mount mapping and readiness already key off `code_workspace_api_root`; prepare must use the same authority. Explicit `root=` in tests remains for isolation.

Alternatives considered:
- Only change `.env` to `code_agent/runs` — rejected; compose/docs/security naming already standardized on `code-agent`.
- Fail closed when API root empty — deferred; unit tests construct `WorkspaceManager()` without full secure settings; hyphen fallback keeps behavior defined.

### 2. Do not auto-migrate orphan directories

Delete `apps/api/data/code_agent/runs` as a one-shot cleanup. No rename/move into `code-agent/runs`.

Rationale: contents are failed-init / debug leftovers; migration could confuse sentinel/run-id assumptions.

### 3. Regression coverage at the seam

Add/adjust a unit test that with configured `code_workspace_api_root`, `WorkspaceManager().root` equals that path (and empty-config fallback uses `code-agent/runs`). Existing mount tests already assert `workspace_mount_invalid` when outside API root.

## Risks / Trade-offs

- [Risk] Environments that relied on the underscore default without setting API root get a new on-disk path → Mitigation: readiness already expects configured dual roots in secure deploys; fallback is documented and hyphenated.
- [Risk] Local API process keeps stale settings until restart → Mitigation: restart API after deploy (agreed).
- [Trade-off] Fallback still allows prepare without explicit API root in tests/dev → Acceptable; production compose sets both roots.

## Migration Plan

1. Land code + tests.
2. Delete orphan `apps/api/data/code_agent/runs` on this machine.
3. Restart local API.
4. Re-run CodeAgent init for a project (e.g. `dbt_test`) and confirm Sandbox starts past the former failure step.
5. Rollback: revert the single default-root change; no schema migration involved.

## Open Questions

None — grilled decisions cover scope.
