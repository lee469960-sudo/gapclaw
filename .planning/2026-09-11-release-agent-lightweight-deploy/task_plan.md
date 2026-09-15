# Task Plan: release-agent-lightweight-deploy

## Source of Truth

Formal implementation scope and completion state are defined only in [`openspec/changes/release-agent-lightweight-deploy/tasks.md`](../../openspec/changes/release-agent-lightweight-deploy/tasks.md). This plan maps those task IDs to execution phases; it does not redefine requirements.

## Goal

Implement the approved lightweight trusted GAP self-deployment loop and constrained Release Agent while retaining the existing Code Agent Docker runtime boundary.

## Next Step

Complete the remaining OpenSpec task 5.4 by resolving the public `gapclaw.online` reachability block, configuring the matching GitHub Actions Hook secret with an authorized credential, triggering one fresh signed tag delivery, and executing the administrator-confirmed rollback evidence path. The production host already has a healthy Runner baseline and host-local Caddy/GAP route; the server still does not use GitHub Runner registration or GitHub egress.

## Current Phase

Phase 5 production evidence is in progress: source and configuration verification are complete; v1.0.12 has built immutable API/Web images; the authorized host has a healthy Runner baseline, validated Caddy reload and cleared callback queue. Remaining proof is gated by public domain reachability, GitHub Actions-secret write authority, and an authenticated administrator rollback exercise.

## Execution Phases

| Phase | OpenSpec task IDs | Status |
|---|---|---|
| 1. Trusted release manifest and GitHub Hook boundary | completed |
| 2. Host Deploy Runner, ingress and recoverable release state | completed |
| 3. GAP Release Agent control plane | completed |
| 4. Release management UI | 4.1–4.3 | completed |
| 5. Documentation, end-to-end verification, and rollout safeguards | 5.1–5.3 completed; 5.4 in progress on the authorized production host |

## Execution Protocol

For every OpenSpec task: implement only its scoped change, run its stated verification, record the actual outcome in `progress.md`, then—and only then—mark its checkbox complete in `tasks.md`.

## Decisions Made

| Decision | Rationale |
|---|---|
| Use `.planning/2026-09-11-release-agent-lightweight-deploy/` | Keeps this change's continuity records isolated from archived work. |
| Keep `tasks.md` as the only formal checklist | Planning files provide execution continuity and actual evidence only. |
| Do not start implementation during initialization | This request authorizes artifact review and planning setup only; `opsx:apply` is required before task 1.1. |
| Preserve existing Code Agent Docker runtime in this change | Release Agent must never use it; moving/removing the existing runtime is explicitly out of scope. |

## Errors Encountered

| Error | Attempt | Resolution |
|---|---:|---|
| User-level Planning with Files recovery script path did not exist | 1 | Located the repository-local skill installation; initialization continues from current OpenSpec artifacts with the failed recovery attempt recorded. |
| `deploy/` directory is read-only, so task 1.1 module creation failed | 1 | Do not repeat the write. Place the testable Runner source in a writable `tools/` package; future installation will copy fixed assets to `/opt/gap-runner/` as specified. |
| Ruby YAML parser warned that the workspace appears world-writable in `PATH` | 1 | YAML parsed successfully; treat this as local-environment hygiene information, not a release-workflow failure. Avoid relying on Ruby for authoritative CI validation. |
| First web-result adapter assumed an MCP-style `content` array | 1 | Re-ran the same read-only official GitHub query with serialized output; no external state was changed. |
| Sandbox denied loopback binding for the mTLS integration test | 1 | Re-ran the same local-only test with approved elevated execution; no external network endpoint was used. |
| API callback test used a thread-local in-memory SQLite database | 1 | Changed that test to a shared `StaticPool` SQLite connection with `check_same_thread=False`; do not use a plain `sqlite:///:memory:` engine with `TestClient`. |
| Release Agent ordering test used identical audit timestamps | 1 | Made test events use distinct timestamps so the newest-record assertion tests the documented ordering instead of ID tie-breaking. |
| API test command ran from `apps/web`, resolving to a Python installation without pytest; root-level combined retry could not find `package.json` | 2 | Run API tests from repository root and invoke the Web build with `npm --prefix apps/web run build`, avoiding both working-directory assumptions. |
| API directory resolved to a Python without pytest | 1 | Ran the API tests from repository root with the explicit `PYTHONPATH=apps/api` import root used by the available pytest environment. |
| Initial OpenSpec Nginx artifact patch did not match an mTLS section | 1 | No partial file was written; split the patch against the actual trusted-deployment spec paragraph and validation then passed. |
| GAP Compose config rendering required `/opt/gap/.env` | 1 | Did not create or alter the production path. Validated the edge Compose directly and exercised GAP ingress declarations through static tests with the required production env contract preserved. |
| Shared edge network allowed a future Compose to reach GAP API | 1 | Reopened task 2.7. Added a Nginx/API-only `gap-proxy` network and kept `gap-edge` for Web plus future explicit Compose hosts. |
| Python 3.13 rejected incomplete temporary test certificates | 2 | Generated a constrained CA plus Subject/Authority Key Identifier and purpose extensions for the temporary leaf certificates. |
| Documentation inspection referenced a nonexistent `tools/gap_deploy_runner/assets/install.sh` | 1 | Use the actual fixed host initializer `initialize-host.sh`; no deployment asset was modified. |
| Documentation replacement patch mixed delete/add operations for one file | 1 | No file changed; use one replacement operation for the existing document and a separate test edit. |
| Documentation contract test lacked the callback's complete fixed URL | 1 | Update the callback paragraph to state the complete URL, then rerun the focused documentation test group. |
| Combined 5.3 API-suite/Web-build command exceeded the tool's initial response window at API 37% | 1 | No test failure was reported; run the API suite separately in a reusable session, then run Web and Runner checks independently. |
