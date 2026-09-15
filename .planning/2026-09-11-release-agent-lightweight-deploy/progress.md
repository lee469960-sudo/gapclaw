# Progress: release-agent-lightweight-deploy

## 2026-09-11 — Planning initialization

- Created a dedicated Planning with Files directory for `release-agent-lightweight-deploy`.
- Established `tasks.md` as the only formal task checklist and mapped task IDs to execution phases without redefining requirements.
- No OpenSpec implementation task has started, no code has changed, and no checkbox in `tasks.md` has been marked complete.
- Read and recorded `proposal.md`, `design.md`, both capability specs, and all 22 items in `tasks.md`.
- Updated `.planning/.active_plan` to this change. No OpenSpec task is complete; no implementation, tests, or task-checkbox updates were performed.
- Next: wait for `opsx:apply` before beginning task 1.1.

## 2026-09-11 — Apply started

- OpenSpec apply context was read in full; schema is `spec-driven`, with 0/22 tasks complete.
- Started Phase 1 / task 1.1. No task checkbox has been changed yet; completion remains contingent on focused model tests.
- Attempted task 1.1 module creation under `deploy/`, but the directory is read-only. No source file was created. The approach is changed to a writable `tools/` package and the failure is recorded in `task_plan.md` and `findings.md`.

## 2026-09-11 — OpenSpec task 1.1 complete

- Implemented the dependency-free manifest contract in `tools/gap_deploy_runner/release_manifest.py`, with fixed schema fields, canonical digest parsing, allowed-repository enforcement, tag/version equality, commit, target, timestamp and health-check-version validation.
- Added focused tests in `tools/gap_deploy_runner/tests/test_release_manifest.py` for a valid round trip, tag/version mismatch, mutable API/Web tags, invalid digest and an unapproved repository.
- Verification: `python3 -m pytest -q tools/gap_deploy_runner/tests/test_release_manifest.py` — 6 passed.
- Code and task-specific tests are complete; task 1.1 is ready to be checked in the formal OpenSpec checklist.

## 2026-09-11 — OpenSpec task 1.2 complete

- Updated `.github/workflows/release.yml` so the tag build requires exact Git tag / `deploy/gap.version` equality, assigns stable API/Web build step ids, creates a canonical digest-pinned release manifest, and uploads it as `gap-release-manifest`.
- Added static workflow assertions in `tools/gap_deploy_runner/tests/test_release_build_workflow.py` for exact version matching, digest outputs and artifact publication.
- Verification: `python3 -m pytest -q tools/gap_deploy_runner/tests/test_release_manifest.py tools/gap_deploy_runner/tests/test_release_build_workflow.py` — 8 passed. Ruby YAML parse also completed successfully (with a non-failing local PATH-permission warning recorded in `task_plan.md`).
- Code and task-specific tests are complete; task 1.2 is ready to be checked in the formal OpenSpec checklist.

## 2026-09-11 — OpenSpec task 1.3 complete

- Added the protected-default-branch `workflow_run` production deployment workflow. It requires a successful same-repository tag Release run, downloads `gap-release-manifest` from that run with explicit `actions: read`, uses the `production` Environment and invokes only `/opt/gap-runner/bin/gap-deploy-runner deploy --manifest`.
- Added workflow assertions proving the production workflow has no tag checkout, repository deploy-script/Compose execution or ACR password input.
- Verification: `python3 -m pytest -q tools/gap_deploy_runner/tests` — 10 passed; Python/PyYAML parsed both `.github/workflows/release.yml` and `.github/workflows/deploy-production.yml` successfully.
- Code and task-specific tests are complete; task 1.3 is ready to be checked in the formal OpenSpec checklist.

## 2026-09-11 — OpenSpec task 1.4 complete

- Removed the tag workflow's legacy production `deploy` job, its now-unused job outputs, self-hosted runner invocation, tag checkout deployment path and ACR credential environment transfer.
- Extended static checks to prove that only the separate default-branch deployment workflow can invoke the fixed host runner, while the tag workflow cannot define a production deploy command.
- Verification: `python3 -m pytest -q tools/gap_deploy_runner/tests` — 11 passed; both workflow YAML files parsed with Python/PyYAML; `git diff --check` — passed.
- Code and task-specific tests are complete; task 1.4 is ready to be checked in the formal OpenSpec checklist. Phase 1 is complete after the formal checkbox update.

## 2026-09-11 — OpenSpec task 2.1 complete

- Added `tools.gap_deploy_runner.runner` with the fixed four-operation Runner command model, strict manifest admission, fixed-target validation, limited status/health state and known-healthy-only rollback boundary.
- Added `tools.gap_deploy_runner.cli` plus focused tests for valid admission, unknown operation rejection, unapproved images, target mismatch, no arbitrary rollback input and CLI command injection rejection.
- Verification: `python3 -m pytest -q tools/gap_deploy_runner/tests` — 18 passed; `python3 -m compileall -q tools/gap_deploy_runner` — passed; `git diff --check` — passed.
- Code and task-specific tests are complete; task 2.1 is ready to be checked in the formal OpenSpec checklist.

## 2026-09-11 — OpenSpec task 2.2 complete

- Added a fixed Runner Compose asset and `RunnerConfig` host-env loader. It derives allowed API/Web repositories from host-only ACR configuration and supplies canonical digest variables plus version to Compose without reading GitHub environment variables.
- Added tests for local credential handling, `GITHUB_*` rejection and immutable Compose API/Web image variables.
- Verification: `python3 -m pytest -q tools/gap_deploy_runner/tests` — 21 passed; `python3 -m compileall -q tools/gap_deploy_runner` — passed; `git diff --check` — passed.
- Code and task-specific tests are complete; task 2.2 is ready to be checked in the formal OpenSpec checklist.

## 2026-09-11 — OpenSpec task 2.3 complete

- Added durable local release-state storage with atomic writes, per-target mutual exclusion, current state, last-known-healthy manifest, five-entry success retention and restart reconciliation.
- Added tests for state round-trip, history pruning, interrupted active state reconciliation and concurrent target-lock serialization.
- Verification: `python3 -m pytest -q tools/gap_deploy_runner/tests` — 25 passed; `python3 -m compileall -q tools/gap_deploy_runner` — passed; `git diff --check` — passed.
- Code and task-specific tests are complete; task 2.3 is ready to be checked in the formal OpenSpec checklist.

## 2026-09-11 — OpenSpec task 2.4 complete

- Added a mockable fixed Compose deployment adapter that accepts only a validated manifest, requires health success, records success, and automatically reapplies the stored last-known-healthy manifest on failure. No baseline produces `reconciliation_required`.
- Verification: `python3 -m pytest -q tools/gap_deploy_runner/tests/test_deployment.py tools/gap_deploy_runner/tests/test_release_state.py` — 7 passed.

## 2026-09-11 — Task 2.4 completion audit reopened

- The first implementation modeled only one combined health boolean. It did not separately exercise Compose service health and API `/health`, so task 2.4 was un-checked and returned to in-progress before continuing.
- Completed the audit: Compose service health and API readiness are now independent probes; `TimeoutError` is a health failure and a healthy rollback baseline is reapplied. Focused deployment tests: 5 passed. Task 2.4 is checked again.

## 2026-09-11 — OpenSpec task 2.5 complete

- Added the Runner's mTLS HTTP listener and fixed route adapter for status, health and input-free rollback. `deploy` remains local CLI-only; no network deploy endpoint exists.
- Added a real loopback mTLS integration test using ephemeral CA, Runner and GAP-client certificates. It verifies that the trusted client gets the fixed status response and a client without a certificate is rejected during TLS authentication. Existing boundary tests verify rollback cannot receive an arbitrary tag/digest.
- Verification: `python3 -m pytest -q tools/gap_deploy_runner/tests` — 32 passed; `python3 -m compileall -q tools/gap_deploy_runner` — passed; `git diff --check` — passed.
- Code and task-specific tests are complete; task 2.5 is ready to be checked in the formal OpenSpec checklist.

## 2026-09-11 — OpenSpec task 2.6 complete

- Added the fixed `gap-deploy-runner` host-layout validator, systemd unit, initialization script and non-secret Runner environment example under the Runner assets package.
- Preflight verifies the local app env, fixed Compose asset, TLS CA/certificate/private key, state directory, Docker Compose availability and restrictive permissions for the env, private key and state directory.
- Added focused tests for every missing prerequisite, unsafe permissions and the hardening/fixed-path content of the systemd unit.
- Verification: `python3 -m pytest -q tools/gap_deploy_runner/tests` — 44 passed; `python3 -m compileall -q tools/gap_deploy_runner` — passed; `git diff --check` — passed.
- Code and task-specific tests are complete; task 2.6 is ready to be checked in the formal OpenSpec checklist. Phase 2 is complete after the formal checkbox update.

## 2026-09-11 — OpenSpec task 3.1 started

- Read the API model, startup-migration and database-test conventions. 3.1 will use dedicated release tables and a service layer; no checkbox has been changed and no 3.1 completion is claimed.

## 2026-09-11 — OpenSpec task 3.1 complete

- Added dedicated release manifest, lifecycle audit, rollback request and callback-delivery persistence models plus the deterministic `ReleaseLedger` service. The existing startup `Base.metadata.create_all` migration path creates the new tables without altering Code Agent persistence.
- Terminal Runner callbacks are idempotent per release/status, sensitive fields are rejected before storage, rollback requests record the operator, and target history is newest-first.
- Verification: `PYTHONPATH=apps/api python3 -m pytest -q apps/api/tests/test_release_ledger.py apps/api/tests/test_startup_migrations.py` — 8 passed; `python3 -m compileall -q apps/api/app` — passed; `git diff --check` — passed. The suite emitted only existing Pydantic/`utcnow` deprecation warnings.
- Code and task-specific tests are complete; task 3.1 is ready to be checked in the formal OpenSpec checklist.

## 2026-09-11 — OpenSpec task 3.2 paused for mTLS ingress decision

- Verified that the current production Compose has only direct HTTP API port exposure and no TLS terminator. The fixed callback endpoint cannot safely treat a client-certificate header as proof without a specified trusted mTLS ingress boundary.
- No 3.2 code or checkbox update was made. Await an explicit choice of host-managed mTLS proxy versus direct API-server TLS before modifying deployment assets or callback authentication.

## 2026-09-11 — Nginx ingress scope accepted

- User confirmed host-managed Nginx and the `gapclaw.online` primary domain. Updated proposal, design, both capability specs and formal tasks; added OpenSpec task 2.7 before 3.2.
- Design fixes `runner.gapclaw.online` as the mTLS-only fixed callback host and reserves the shared edge network for later explicit second-Compose domains without preconfiguring an unknown route.
- Verification: `openspec validate release-agent-lightweight-deploy --strict` — passed. No implementation task checkbox changed.

## 2026-09-11 — OpenSpec task 2.7 complete

- Added host-managed edge Compose and Nginx configuration for `gapclaw.online`, `runner.gapclaw.online`, shared `gap-edge` networking, explicit GAP service aliases and the single mTLS callback route.
- GAP API now exposes its health port to host loopback only; Web has no direct host port. The edge configuration rejects unknown HTTP/HTTPS hosts and all non-callback Runner paths, while mTLS requires the configured Runner CA.
- Verification: `python3 -m pytest -q tools/gap_deploy_runner/tests` — 47 passed; `python3 -m compileall -q tools/gap_deploy_runner` — passed; `docker compose -f tools/gap_deploy_runner/assets/gap-edge.compose.yml config` — passed; `git diff --check` — passed. GAP Compose render intentionally remains dependent on the real `/opt/gap/.env` and was not bypassed.
- Code and task-specific tests are complete; task 2.7 is ready to be checked in the formal OpenSpec checklist. Phase 2 is complete after the formal checkbox update.

## 2026-09-11 — Task 2.7 completion audit reopened and completed

- The first edge topology put GAP API on shared `gap-edge`, allowing a future stack member to bypass Nginx and forge its injected client-verification header. Task 2.7 was immediately un-checked and returned to in-progress.
- Corrected the topology: Nginx bridges shared `gap-edge` and private `gap-proxy`; only Nginx and GAP API join `gap-proxy`, while GAP Web and future Compose stacks use `gap-edge` only.
- Verification: `python3 -m pytest -q tools/gap_deploy_runner/tests/test_nginx_edge_assets.py tools/gap_deploy_runner/tests/test_runner_config.py` — 6 passed; `docker compose -f tools/gap_deploy_runner/assets/gap-edge.compose.yml config` — passed; `openspec validate release-agent-lightweight-deploy --strict` — passed; `git diff --check` — passed.
- Code and task-specific tests are complete; task 2.7 is ready to be checked again in the formal OpenSpec checklist.

## 2026-09-11 — OpenSpec task 3.2 in progress

- Added the fixed GAP-side mTLS client boundary and callback service. The client allows only the configured Runner host and uses CA/client-certificate paths; the callback service requires Nginx's verified-client result and preserves `ReleaseLedger` idempotency.
- Focused verification: `PYTHONPATH=apps/api python3 -m pytest -q apps/api/tests/test_release_runner_protocol.py apps/api/tests/test_release_ledger.py` — 5 passed; `python3 -m compileall -q apps/api/app` — passed; `git diff --check` — passed. Existing Pydantic/`utcnow` deprecation warnings remain.
- Remaining: fixed FastAPI callback route, persisted retry and Runner/GAP reconciliation. No task checkbox changed.

## 2026-09-11 — OpenSpec task 3.2 complete

- Added the fixed `/internal/release-runner/callback` API route. It accepts only the complete fixed digest-pinned envelope, rejects unknown fields or invalid digest payloads, and requires Nginx's `SUCCESS` client-certificate verification result before writing the idempotent manifest/audit records.
- GAP's Runner client is constrained to `https://runner.gapclaw.online` and its three fixed operations with CA and client-certificate verification. Runner now persists terminal callbacks before delivery, applies delayed exponential retry after transport failure, retries during subsequent fixed Runner mTLS access, and exposes no new operation or route. GAP reconciliation reports missing, non-terminal, or invalid Runner records as `reconciliation_required` rather than resuming work.
- Verification: `PYTHONPATH=apps/api python3 -m pytest -q apps/api/tests/test_release_runner_protocol.py apps/api/tests/test_release_ledger.py` — 8 passed (existing Pydantic/`utcnow` warnings only); `python3 -m pytest -q tools/gap_deploy_runner/tests` — 51 passed, including the loopback mTLS integration test; API and Runner `compileall`, `git diff --check`, callback-route registration assertion, and `openspec validate release-agent-lightweight-deploy --strict` all passed.
- Code and task-specific tests are complete; task 3.2 is ready to be checked in the formal OpenSpec checklist.

## 2026-09-11 — OpenSpec task 3.3 complete

- Registered the deterministic `Release Agent` as a separate system definition rather than an `Agent` table record, so it cannot enter the generic ReAct, MCP, command or Code Agent paths. Its only capabilities are archived release status, history and fixed human-readable explanations.
- Added authenticated, GET-only `/api/release-management/status` and `/api/release-management/history` endpoints. They return the system definition and redacted fixed audit fields; no write or execution endpoint was introduced.
- Verification: `PYTHONPATH=apps/api python3 -m pytest -q apps/api/tests/test_release_agent.py apps/api/tests/test_release_runner_protocol.py apps/api/tests/test_release_ledger.py` — 10 passed; API `compileall`, route-registration assertion, and `git diff --check` passed. Existing Pydantic/`utcnow` deprecation warnings remain.
- Code and task-specific tests are complete; task 3.3 is ready to be checked in the formal OpenSpec checklist. Task 3.5 is the necessary fixed-client configuration prerequisite before implementing 3.4's administrator rollback invocation.

## 2026-09-11 — OpenSpec task 3.5 complete

- Added host-provisioned Release Management settings for the fixed target, Runner URL, CA bundle, GAP client certificate/key references and bounded timeout. The runtime configuration fails closed before producing a Runner client if the target, URL, paths or timeout are invalid.
- Added a read-only redacted configuration view. It exposes only target, fixed Runner URL, timeout, readiness and configured booleans; certificate paths, key contents, ACR credentials and unrelated application secrets are never returned, logged or persisted to release audit records. Missing host references report a safe unavailable state rather than causing an API error.
- Verification: `PYTHONPATH=apps/api python3 -m pytest -q apps/api/tests/test_release_config.py apps/api/tests/test_release_agent.py apps/api/tests/test_release_runner_protocol.py apps/api/tests/test_release_ledger.py` — 14 passed; API `compileall`, `git diff --check`, and `openspec validate release-agent-lightweight-deploy --strict` passed. Existing Pydantic/`utcnow` deprecation warnings remain.
- Code and task-specific tests are complete; task 3.5 is ready to be checked in the formal OpenSpec checklist.

## 2026-09-11 — OpenSpec task 3.4 complete

- Added administrator-only rollback-target and rollback APIs. GAP first reads the Runner's persisted `last_known_healthy` target, requires the caller to submit exactly that displayed release/target plus the fixed `ROLLBACK` confirmation phrase, records the requesting operator, and invokes only the input-free Runner rollback operation.
- The Runner status adapter now exposes only the persisted healthy release id/target needed for this control; it does not expose an image, digest, command or mutable deployment input. Invalid configuration returns a safe 503, while no baseline, target mismatch and invalid confirmation are rejected before any Runner rollback call.
- Verification: `PYTHONPATH=apps/api python3 -m pytest -q apps/api/tests/test_release_rollback.py apps/api/tests/test_release_config.py apps/api/tests/test_release_agent.py apps/api/tests/test_release_runner_protocol.py apps/api/tests/test_release_ledger.py` — 18 passed; `python3 -m pytest -q tools/gap_deploy_runner/tests` — 52 passed including loopback mTLS integration; API/Runner `compileall` and `git diff --check` passed. Existing Pydantic/`utcnow` warnings remain.
- Code and task-specific tests are complete; task 3.4 is ready to be checked in the formal OpenSpec checklist. Phase 3 completes after the formal checkbox update.

## 2026-09-11 — OpenSpec task 4.1 complete

- Added the Release Agent / 发布管理 route, menu entry and role-permission option. The new read-only view renders current release status, API/Web digest, health result, automatic rollback result, failure summary and chronological history; `reconciliation_required` is shown as an explicit warning that no automatic continuation occurs.
- The view reads only the existing status/history endpoints and does not receive Runner configuration, certificates, credentials or a deployment execution capability.
- Verification: `PYTHONPATH=apps/api python3 -m pytest -q apps/api/tests/test_release_management_ui.py apps/api/tests/test_release_agent.py apps/api/tests/test_release_rollback.py apps/api/tests/test_release_config.py` — 12 passed; `npm --prefix apps/web run build` — passed; `git diff --check` and `openspec validate release-agent-lightweight-deploy --strict` passed. Existing Vite chunk-size and third-party PURE-comment warnings remain.
- Code and task-specific tests are complete; task 4.1 is ready to be checked in the formal OpenSpec checklist.

## 2026-09-11 — OpenSpec task 4.2 complete

- Added an administrator-only, two-step rollback control to Release Management. It loads and displays only the Runner-reported known-healthy release/target, and the confirmation dialog sends those displayed identifiers unchanged; it offers no image, digest or arbitrary-version field.
- The confirmation action is disabled unless the operator enters exact `ROLLBACK`. The existing server-side authorization, displayed-target comparison and fixed Runner operation remain authoritative; a non-administrator neither sees nor requests the rollback control.
- Verification: `PYTHONPATH=apps/api python3 -m pytest -q apps/api/tests/test_release_management_ui.py apps/api/tests/test_release_rollback.py apps/api/tests/test_release_agent.py apps/api/tests/test_release_config.py` — 13 passed; `npm --prefix apps/web run build`, `git diff --check`, and `openspec validate release-agent-lightweight-deploy --strict` — passed. Existing Pydantic/`utcnow`, Vite chunk-size and third-party PURE-comment warnings remain.
- Code and task-specific tests are complete; task 4.2 is ready to be checked in the formal OpenSpec checklist.

## 2026-09-11 — OpenSpec task 4.3 complete

- The release-management view does not query or render deployment configuration. Its contract test asserts that client-key/CA references and the configuration endpoint are absent from the component.
- Reconciliation and unavailable-Runner states now tell operators to refresh and complete status reconciliation, explicitly avoiding a false-success claim. The raw unavailable detail is not rendered; an existing no-healthy-baseline condition receives a precise safe explanation.
- Verification: `PYTHONPATH=apps/api python3 -m pytest -q apps/api/tests/test_release_management_ui.py apps/api/tests/test_release_rollback.py apps/api/tests/test_release_agent.py apps/api/tests/test_release_config.py` — 14 passed; `npm --prefix apps/web run build`, `git diff --check`, and `openspec validate release-agent-lightweight-deploy --strict` — passed. Existing Pydantic/`utcnow`, Vite chunk-size and third-party PURE-comment warnings remain.
- Code and task-specific tests are complete; task 4.3 is ready to be checked in the formal OpenSpec checklist.

## 2026-09-11 — OpenSpec task 3.5 reopened

- During task 5.1 documentation review, the previous fixed public Runner URL was found to collide with the callback-only Nginx vhost. The user approved separating the public callback endpoint from GAP's private Runner control endpoint.
- Updated the OpenSpec proposal, design and both capability specs: GAP-to-Runner status/health/rollback now use private `https://gap-runner.internal:9443`; `runner.gapclaw.online` remains callback-only. Reopened task 3.5 because its configuration validation and deployment topology must now implement this corrected contract.
- No code or verification is claimed for the reopened task; its formal checkbox was cleared before implementation.

## 2026-09-11 — OpenSpec task 3.5 re-complete

- Replaced the conflicting public Runner control URL with the fail-closed private `https://gap-runner.internal:9443` contract. The API service alone receives a Docker host-gateway mapping and read-only host-provisioned mTLS material; the public callback vhost remains callback-only.
- Added contract coverage that rejects the public callback hostname as a Runner control URL, verifies the private mTLS request URL, API-only host-gateway/mTLS Compose declarations, and absence of `/v1/*` proxy paths in Nginx.
- Verification: `PYTHONPATH=apps/api python3 -m pytest -q apps/api/tests/test_release_config.py apps/api/tests/test_release_runner_protocol.py apps/api/tests/test_release_rollback.py apps/api/tests/test_release_agent.py` — 15 passed; `python3 -m pytest -q tools/gap_deploy_runner/tests/test_nginx_edge_assets.py tools/gap_deploy_runner/tests/test_runner_config.py tools/gap_deploy_runner/tests/test_runner_installation.py` — 18 passed; API/Runner `compileall`, `git diff --check`, and `openspec validate release-agent-lightweight-deploy --strict` — passed. Existing Pydantic/`utcnow` warnings remain.
- Code and task-specific tests are complete; reopened task 3.5 is ready to be checked again in the formal OpenSpec checklist.

## 2026-09-11 — OpenSpec task 5.1 complete

- Replaced the production deployment document with the protected manifest/Environment flow, fixed Runner installation paths, host-only ACR pull configuration, private control/public callback topology, mTLS rotation, initial baseline and emergency rollback procedure.
- The documentation explicitly preserves the Code Agent Docker boundary: Release Agent receives no Docker, shell, SSH, MCP, arbitrary URL/command or ACR-write capability.
- Verification: `python3 -m pytest -q tools/gap_deploy_runner/tests/test_runner_installation.py tools/gap_deploy_runner/tests/test_runner_config.py tools/gap_deploy_runner/tests/test_nginx_edge_assets.py tools/gap_deploy_runner/tests/test_release_build_workflow.py` — 24 passed; `git diff --check` and `openspec validate release-agent-lightweight-deploy --strict` — passed. The first documentation test omitted the complete callback URL, was corrected without changing implementation assets, then the focused suite passed.
- Code and task-specific tests are complete; task 5.1 is ready to be checked in the formal OpenSpec checklist.

## 2026-09-11 — OpenSpec task 5.2 complete

- Added a cross-layer contract test from immutable manifest through health-gated deployment and persistent callback retry into GAP's release ledger/reconciler. It covers healthy digest deployment, failed-health automatic rollback, temporary GAP unavailability, callback retry after Runner restart and synchronized reconciliation.
- Persisted fixed terminal Runner phases before callback emission, so an automatic rollback or no-baseline outcome is durable rather than remaining an active `received` state until a later restart.
- Verification: `PYTHONPATH=apps/api python3 -m pytest -q apps/api/tests/test_release_end_to_end_contract.py apps/api/tests/test_release_runner_protocol.py apps/api/tests/test_release_ledger.py` — 9 passed; `python3 -m pytest -q tools/gap_deploy_runner/tests/test_deployment.py tools/gap_deploy_runner/tests/test_release_state.py tools/gap_deploy_runner/tests/test_result_callback.py` — 12 passed; API/Runner `compileall`, `git diff --check`, and `openspec validate release-agent-lightweight-deploy --strict` — passed. Existing Pydantic/`utcnow` warnings remain.
- Code and task-specific tests are complete; task 5.2 is ready to be checked in the formal OpenSpec checklist.

## 2026-09-11 — OpenSpec task 5.3 complete

- Full verification: `PYTHONPATH=apps/api python3 -m pytest -q apps/api/tests` — 1,153 passed, 4 skipped; `python3 -m pytest -q tools/gap_deploy_runner/tests` — 53 passed after approved local loopback execution; `npm --prefix apps/web run build` — passed.
- `docker compose -f tools/gap_deploy_runner/assets/gap-edge.compose.yml config` passed. The actual production Compose intentionally failed closed without host-owned `/opt/gap/.env`; a non-secret temporary copy with only the env-file path redirected rendered successfully with representative immutable digest inputs and was removed immediately.
- `git diff --check` and `openspec validate release-agent-lightweight-deploy --strict` passed. Existing Pydantic/`utcnow`, Vite chunk-size and third-party PURE-comment warnings remain.
- Code and task-specific tests are complete; task 5.3 is ready to be checked in the formal OpenSpec checklist.

## 2026-09-11 — OpenSpec task 5.4 pending environment authorization

- Read-only repository and Docker inventory checks found no designated non-production GAP Runner, Compose stack, protected Environment or staging release configuration. Existing local containers are unrelated and were not touched.
- The controlled exercise requires a user-authorized staging target; it has not been performed and task 5.4 remains unchecked.

## 2026-09-11 — OpenSpec task 2.7 re-complete (production Caddy migration)

- Replaced the production Nginx edge assets with a host-installed `Caddyfile.production` and guarded `install-production-caddy.sh`. `gapclaw.online` reaches loopback-only API/Web upstreams, while `runner.gapclaw.online` exposes only the mTLS-verified callback and rejects all other paths.
- The production Compose asset now binds API and Web only to `127.0.0.1`, removes `gap-edge`/`gap-proxy` and creates no public proxy container/network. The Caddy installer requires the reviewed site import and production-only callback CA, validates the host Caddyfile before reload, and never starts or loads Nginx.
- Verification: `python3 -m pytest -q tools/gap_deploy_runner/tests/test_production_caddy_assets.py tools/gap_deploy_runner/tests/test_runner_installation.py tools/gap_deploy_runner/tests/test_release_build_workflow.py` — 25 passed; `git diff --check` and `openspec validate release-agent-lightweight-deploy --strict` — passed.
- Code and task-specific tests are complete; revised task 2.7 is ready to be checked in the formal OpenSpec checklist.

## 2026-09-11 — OpenSpec task 5.1 re-complete (production Caddy documentation)

- Updated `docs/deployment.md` to remove the old production Nginx network/CA layout and document host Caddy install/validate/reload flow, the reviewed `/etc/caddy/sites/*.Caddyfile` import, production callback CA path, loopback upstreams, and the explicit-domain extension boundary for a second Compose stack.
- The documentation contract now rejects the retired `gap-edge`, `gap-proxy`, and `/opt/gap-edge` paths while retaining an explicit statement that the installation does not start or load Nginx.
- Verification: `python3 -m pytest -q tools/gap_deploy_runner/tests/test_production_caddy_assets.py tools/gap_deploy_runner/tests/test_runner_installation.py tools/gap_deploy_runner/tests/test_release_build_workflow.py` — 25 passed; `git diff --check` and `openspec validate release-agent-lightweight-deploy --strict` — passed.
- Code and task-specific tests are complete; revised task 5.1 is ready to be checked in the formal OpenSpec checklist.

## 2026-09-11 — OpenSpec task 5.3 re-complete (production Caddy migration)

- Runner regression passed: `python3 -m pytest -q tools/gap_deploy_runner/tests --ignore=tools/gap_deploy_runner/tests/test_mtls_integration.py` — 71 passed. The isolated loopback mTLS integration test initially hit the workspace socket restriction, then passed with approved local-only loopback permission: 1 passed.
- API regression passed: `PYTHONPATH=apps/api python3 -m pytest -q apps/api/tests` — 1,159 passed, 4 skipped. Web production build passed: `npm --prefix apps/web run build`. Existing Pydantic/`utcnow`, Vite chunk-size and third-party PURE-comment warnings remain non-failing.
- Production Caddy/Compose static contract checks passed in the focused 25-test suite, including loopback-only API/Web publication, no Nginx/gap-edge/gap-proxy assets, callback-only mTLS site, reviewed Caddy import and validate-before-reload installer. `git diff --check` and `openspec validate release-agent-lightweight-deploy --strict` passed.
- Code and verification are complete; revised task 5.3 is ready to be checked in the formal OpenSpec checklist. Task 5.4 remains the only unchecked task.

## 2026-09-12 — Production host initialization started (operational rollout)

- User authorized `124.221.215.177` as the production initialization host and authorized replacement of the temporary `gapclaw.online` Caddy proxy. Read-only preflight confirmed Docker/Compose, a healthy existing initialization service, Caddy on 80/443, and DNS for both production browser/callback hosts.
- The current host Caddyfile also owns `light.gapclaw.online`; production rollout must preserve it. Caddy 2.6.2 requires compatible upgrade before the reviewed mTLS site can be installed. No Caddy, Runner, Compose, PKI or GitHub state has been modified yet.
- This is operational rollout evidence only. OpenSpec task 5.4 remains unchecked because it specifically requires a separate controlled non-production exercise.

## 2026-09-12 — Production initialization paused before deployment

- Safely upgraded host Caddy to v2.11.4 after backing up the existing binary/configuration. Existing Caddy validation and service health passed. The current `gapclaw.online` initialization service is an OpenClaw Compose project on loopback port 18789; GAP ports are available and `light.gapclaw.online` remains preserved.
- The current source revision is tagged `v1.0.9`, but the reviewed Release Agent/production workflow/deployment assets are uncommitted in the local workspace. No GitHub build-verified manifest can therefore represent these changes.
- Paused before installing a GAP site, Runner, Compose, PKI or GitHub Runner. Deployment must wait for explicit authorization to make a scoped commit/push and create a matching release tag; no formal OpenSpec task checkbox changed.

## 2026-09-12 — Release source published; production initialization resumed

- Created focused commit `fa920e2` (`feat(release): add trusted deployment control plane`), corrected `deploy/gap.version` to the exact lowercase tag `v1.0.10`, and pushed both `main` and tag `v1.0.10` to the authorized repository.
- The tag has triggered the GitHub Release build. No GAP Caddy route, Runner, Compose state, PKI, or production application image has yet been changed after the push.
- Next: verify the successful build's artifact and digest-pinned manifest, then provision the reviewed fixed host assets. This production rollout does not satisfy the separate non-production requirement in OpenSpec task 5.4.

## 2026-09-12 — Release manifest verified; host setup gated on ACR authentication

- GitHub Release run `34626456976` completed successfully for `fa920e2`. Its `gap-release-manifest` artifact is exact-tagged `v1.0.10`, target `production`, and contains approved immutable ACR API/Web image digests.
- Installed the fixed Deploy Runner files and systemd unit under `/opt/gap-runner`; it is explicitly `disabled` and `inactive`. Verified the production Docker bridge is `172.17.0.1/16`, port 9443 is free, and Caddy remains healthy. Downloaded and checksum-verified the official Linux x64 GitHub Actions Runner archive, but did not register it, so no deployment job can start prematurely.
- Production dependency check found Docker Hub unavailable from the host, so the database image must use the authorized ACR path. The supplied ACR authentication was rejected with HTTP 401 and the local deployment environment has no saved ACR pull credential. No `/opt/gap/.env`, Docker login, mTLS material, firewall rule, Caddy production site, GAP Compose project, or GitHub self-hosted Runner registration has been created.
- OpenSpec task 5.4 remains unchecked: this production work is not a controlled non-production exercise.

## 2026-09-12 — OpenSpec task 2.6 revalidated after production installation finding

- Production installation exposed an asset-contract defect: `initialize-host.sh` installed `gap-prod.compose.yml`, while `RunnerInstallation` correctly requires the target-derived `gap-production.compose.yml`. Task 2.6 was reopened before any workaround or GitHub Runner registration.
- Corrected the initializer's destination filename and added an asset-contract test. Focused verification: `python3 -m pytest -q tools/gap_deploy_runner/tests/test_runner_installation.py tools/gap_deploy_runner/tests/test_runner_config.py tools/gap_deploy_runner/tests/test_staging_runner_assets.py` — 21 passed; Runner `compileall` and `git diff --check` passed.
- Applied the same fixed asset name on the authorized host, restarted the service, and verified it is active, bound only to `172.17.0.1:9443`, and returns the expected empty fixed `status` response. The host ACR credential now authenticates as the `gap-runner` account. Because the old ACR database image was arm64-only and Docker Hub is unreachable from this host, published the official linux/amd64 pgvector base image to the authorized ACR namespace and pinned its resulting digest in the host-only environment.
- Code and tests are complete, and the real host installation now satisfies this task's fixed layout; task 2.6 is ready to be checked again. Task 5.4 remains unchecked.

## 2026-09-12 — GitHub production Runner registration blocked by credential scope

- The successful production deployment workflow is queued and waiting for a runner with the `production` label. The host's GitHub Actions Runner archive is checksum-verified and unpacked, but remains unconfigured and stopped.
- The supplied GitHub PAT can read workflow state but GitHub rejected `POST /repos/lee469960-sudo/gapclaw/actions/runners/registration-token` with HTTP 403. No registration token, registered runner, GitHub job execution, Caddy routing change, or GAP Compose deployment resulted from that request.
- Next requires a repository-owner credential with permission to administer self-hosted runners, or an administrator-generated one-time registration token. Existing production Caddy routing stays unchanged until the protected job can establish a healthy private baseline.

## 2026-09-12 — Supplied one-time Runner token rejected

- Attempted to configure `/opt/actions-runner` with the user-supplied one-time token, the fixed repository URL and only the `production` label. GitHub's runner registration endpoint returned HTTP 404.
- The Runner configuration stopped before writing a `.runner` file or starting a service; no queued GitHub job, GAP Compose deployment, Caddy change or callback route was triggered.
- A new token must be generated for this exact repository and used before expiry. This is distinct from the earlier PAT scope failure; no deployment control boundary was weakened to work around either error.

## 2026-09-12 — Hook architecture accepted and formal scope revised

- User confirmed that production must not depend on server-to-GitHub access. The formal change now replaces `workflow_run` and GitHub self-hosted Runner with GitHub-hosted CI posting a fixed HMAC-signed manifest, delivery id and bounded timestamp to GAP's Hook.
- GAP Release Intake is non-LLM: it validates and deduplicates the Hook before using its fixed mTLS identity to call Runner deploy. The LLM-facing Release Agent remains read-only; it cannot choose a target, image, command, Hook URL or signature material.
- OpenSpec strict validation passed after reopening affected tasks 1.2–1.4, 2.5, 2.7, 3.2 and 5.1–5.4. No implementation code, Caddy site or production deployment changed during this planning revision.

## 2026-09-12 — OpenSpec task 1.2 complete (signed GitHub CI Hook)

- The tag build now creates a canonical JSON envelope containing its immutable manifest, a deterministic GitHub delivery id and a UTC timestamp; it signs the exact bytes with the `GAP_RELEASE_HOOK_SECRET` HMAC-SHA256 secret and posts only to the fixed `https://gapclaw.online/internal/release-hook` endpoint.
- Removed the generated staging manifest from this production release workflow. The Hook delivery step has no ACR username/password variables and cannot invoke a host command, Compose file or deployment script; registry credentials remain scoped to the build login action.
- Verification: `pytest -q tools/gap_deploy_runner/tests/test_release_build_workflow.py` — 4 passed. The source checks cover exact tag/version validation, digest manifest construction, canonical HMAC envelope/fixed Hook and absence of registry credentials or tag-controlled host execution in delivery.

## 2026-09-12 — OpenSpec task 1.3 complete (retire GitHub Runner workflows)

- Removed the production and staging `workflow_run` deployment workflows. Release now runs only on GitHub-hosted `ubuntu-latest`; no remaining workflow targets `self-hosted`, a production GitHub Environment, a runner registration endpoint or a host command.
- Verification: `pytest -q tools/gap_deploy_runner/tests/test_release_build_workflow.py` — 5 passed; a source scan of `.github/workflows` found no `workflow_run`, `self-hosted` or runner-registration dependency. The production host has no CI-side requirement for GitHub egress or a Runner token.

## 2026-09-12 — OpenSpec task 1.4 complete (fixed Hook-only workflow contract)

- Added an explicit workflow contract test: the target is statically `production`; delivery cannot consume the Git tag as a command or endpoint and contains no Runner path, Compose input or deployment script. The only delivery URL is the reviewed fixed GAP Hook.
- Verification: `pytest -q tools/gap_deploy_runner/tests/test_release_build_workflow.py` — 6 passed; `git diff --check` passed.

## 2026-09-12 — OpenSpec task 2.5 complete (mTLS Runner deploy endpoint)

- Runner now exposes fixed `POST /v1/deploy` alongside status, health and restricted rollback. It accepts a bounded JSON body only, passes only the complete manifest to the existing strict Runner validator, and rejects arbitrary fields, missing payloads, unrecognised paths and target/query variations.
- The TLS server extracts the client certificate common name and accepts the fixed GAP identity (`gap-client`) only; a different client signed by the same CA is rejected before callback retry or deployment. No caller can provide a command, image repository override, tag, target or Compose path.
- Verification: `pytest -q tools/gap_deploy_runner/tests/test_mtls_api.py tools/gap_deploy_runner/tests/test_mtls_integration.py` — 6 passed (the mTLS integration portion used approved temporary loopback binding); `git diff --check` passed.

## 2026-09-12 — OpenSpec task 2.7 complete (fixed Caddy Hook route)

- Added an exact `POST /internal/release-hook` matcher on `gapclaw.online` that proxies only to loopback GAP API. The same path under every other method returns 404 before the Web fallback. The private Runner control paths remain absent from Caddy; the separate `runner.gapclaw.online` callback keeps its mTLS-only route.
- Verification: `pytest -q tools/gap_deploy_runner/tests/test_production_caddy_assets.py tools/gap_deploy_runner/tests/test_runner_installation.py` — 18 passed; `git diff --check` passed. The checks retain loopback-only API/Web bindings, no Nginx assets, no production catch-all/subdomain collision and certificate-gated Runner callback behavior.

## 2026-09-12 — OpenSpec task 3.2 complete (GAP signed Hook intake and Runner dispatch)

- Added the non-LLM `/internal/release-hook` receiver. It validates the raw canonical envelope with `GAP_RELEASE_HOOK_SECRET` HMAC-SHA256, delivery-id form, bounded timestamp, fixed target, tag/version, commit SHA and immutable API/Web digest fields before any database or Runner action. A missing/invalid secret fails closed and is never exposed through an API response or audit record.
- Accepted deliveries are durable and unique by both delivery id and release id before `POST /v1/deploy`; replays never create a second deploy. GAP passes only the validated manifest over its existing fixed mTLS client. Runner-request failure is recorded as a constrained dispatch state for reconciliation; the established fixed Runner callback retry/reconciliation contract remains unchanged.
- Verification: `PYTHONPATH=apps/api pytest -q apps/api/tests/test_release_hook.py apps/api/tests/test_release_runner_protocol.py apps/api/tests/test_release_ledger.py apps/api/tests/test_release_end_to_end_contract.py` — 19 passed; `git diff --check` passed. The API tests cover signature/timestamp/canonical/replay/target rejection, trusted dispatch, dispatch failure audit and the fixed mTLS deploy path.

## 2026-09-12 — OpenSpec task 5.1 complete (Hook deployment operations documentation)

- Updated the production guide for the GitHub CI HMAC Hook, exact Caddy Hook route, host-only ACR pull credentials, no GitHub Runner/server GitHub egress, fixed GAP-to-Runner mTLS deploy, certificate rotation, first healthy baseline, reconciliation and Release Agent permission boundary. It now names the actual `RELEASE_ENVIRONMENT`, TLS and Hook settings rather than retired runner-workflow configuration.
- Verification: `pytest -q tools/gap_deploy_runner/tests/test_runner_installation.py tools/gap_deploy_runner/tests/test_release_build_workflow.py tools/gap_deploy_runner/tests/test_production_caddy_assets.py` — 24 passed; `git diff --check` passed. The documentation contract checks the real Hook URL/secret/config paths and rejects stale self-hosted/workflow-run references.

## 2026-09-12 — OpenSpec task 5.2 complete (CI Hook-to-Runner end-to-end contract)

- Added a cross-layer contract that sends a canonical HMAC-signed Hook envelope through GAP intake into a Runner adapter that accepts only the resulting manifest. It proves digest deployment establishes a healthy baseline, a failed health check restores that baseline, and a failed Runner callback is persisted then retried without replaying the delivery.
- Verification: `PYTHONPATH=apps/api pytest -q apps/api/tests/test_release_hook_end_to_end_contract.py apps/api/tests/test_release_hook.py` — 8 passed. Existing tests also cover private mTLS transport separately, so this contract remains host/network independent while preserving the same fixed data boundary.

## 2026-09-12 — OpenSpec task 5.3 complete (release configuration regression checks)

- Runner verification: `pytest -q tools/gap_deploy_runner/tests` — 73 passed, including local mTLS integration. Affected API release suites: 33 passed. CI Hook/Caddy/Compose/installation contracts: 27 passed. Web production build: `npm --prefix apps/web run build` passed.
- `openspec validate release-agent-lightweight-deploy --strict` and `git diff --check` passed. The API full-suite runner is subject to this execution environment's 30-second output cutoff, so this task records the complete affected release API suites rather than claiming an unobserved full-suite result. Existing non-failing Pydantic `utcnow`, Vite chunk-size and third-party PURE-comment warnings remain outside this change.

## 2026-09-12 — OpenSpec task 5.4 pending external non-production evidence

- Implementation and local contract evidence cannot replace the required controlled non-production rollout. The only authorized remote host is production and must not be repurposed as staging evidence.
- The remaining requirement, missing evidence, one unexecuted staging-host Hook exercise and its success criteria are recorded in `findings.md` under “Task 5.4 Current Non-production Exercise Gap”. No checkbox was changed.

## 2026-09-12 — OpenSpec task 5.4 switched to authorized production rollout

- The user explicitly authorized a direct controlled production exercise on `gapclaw.online`; the formal task and migration plan now require production rather than staging evidence. No checkbox changed.
- Release tag `v1.0.12` (`7ada9c2`) produced immutable API/Web images and its `gap-release-manifest` artifact was retrieved and verified. Its delivery step failed before host bootstrap because the new Hook was not yet live; the build and artifact steps passed, so this is recorded as expected bootstrap evidence rather than a release success.
- The GitHub credential currently available can read the artifact but is insufficient to write Actions secrets. The rollout continues with host asset/bootstrap and health validation; final CI-Hook proof remains pending the scoped Actions-secret permission described in `findings.md`.

## 2026-09-12 — Production bootstrap blocked by application startup migration; v1.0.13 released

- Verified the v1.0.12 asset archive/manifest checksums on the authorized host, upgraded the fixed Runner assets, preserved Runner and Caddy backups, and reissued host-only Runner/GAP mTLS materials after discovering the previous client CN did not satisfy the fixed `gap-client` identity. The independent mTLS status route succeeds.
- A v1.0.12 baseline attempt remained `reconciliation_required`: it did not establish a false healthy release. API logs identified `code_project_manifests.status VARCHAR(16)` rejecting the required `security_republish_required` value during startup. No Caddy route was changed, so public `gapclaw.online` remains on the prior service.
- Implemented and verified the PostgreSQL-compatible width migration with `PYTHONPATH=apps/api pytest -q apps/api/tests/test_startup_migrations.py apps/api/tests/test_code_agent_secure_schema.py` — 13 passed; `git diff --check` passed. Published `bd3d3e1` as tag `v1.0.13`; its GitHub Release build is in progress. Task 5.4 remains unchecked.

## 2026-09-12 — Production Runner/Caddy bootstrap evidence; 5.4 still incomplete

- Published additional focused Runner/Caddy fixes through tags `v1.0.14`–`v1.0.18`: Compose health waits, managed Compose working directory, manifest env retention for health checks, Compose v5 line-delimited JSON parsing, and fixed callback TLS server-certificate configuration. Focused verification included `pytest -q tools/gap_deploy_runner/tests/test_runtime.py tools/gap_deploy_runner/tests/test_deployment.py tools/gap_deploy_runner/tests/test_runner_installation.py` — 24 passed, and `pytest -q tools/gap_deploy_runner/tests/test_production_caddy_assets.py tools/gap_deploy_runner/tests/test_runner_installation.py` — 18 passed.
- On the authorized production host, `v1.0.17` Runner assets were checksum-verified (`794fa4744b4682473d41e3a8b8c8609dda78f0726dc8ba16bbf19d91e6a1a713`) and installed. The verified `v1.0.12` manifest checksum (`6b4ae3aac25ef4789f977d37f6c7e7a2f2471c95551851aaf5da35be8fa357d7`) deployed through the fixed Runner and established `phase=succeeded`, `last_known_healthy=v1.0.12-7ada9c27`.
- Production Caddy was reloaded only after validation, with backup `/var/backups/gap-caddy/20260911T234917Z`. Local SNI validation on the host proved `https://gapclaw.online/health` routes through Caddy to GAP and returns `{"status":"ok","version":"v1.0.12"}`; private Runner mTLS `health` and `status` returned `status=ok` with the same release and last-known-healthy baseline.
- `v1.0.18` fixed the callback host to use a release-CA-signed server certificate instead of ACME. The production host installed that asset, generated `/etc/caddy/production/runner-server.crt/key`, mapped `runner.gapclaw.online` to local Caddy for host-origin callbacks, and reissued the Runner certificate with both `serverAuth` and `clientAuth`. Callback checks proved unauthenticated clients are rejected at TLS, trusted clients reach GAP, and Runner's dispatcher delivered the two pending callbacks; Runner state ended with `pending_callbacks=0`.
- Public `http://gapclaw.online/health` still returns the Tencent/DNSPod webblock redirect and public `https://gapclaw.online/health` resets during TLS ClientHello. This blocks external GitHub CI Hook proof and browser-domain acceptance even though the host-local Caddy/GAP route is healthy. The 5.4 checkbox remains unchecked.

## 2026-09-12 — v1.0.19 pre-release fixes for local LLM connectivity and fixed application chrome

- Fixed the local-model connectivity failure where API containers could not reach user-configured `localhost`/`127.0.0.1` OpenAI-compatible endpoints. Runtime LLM calls now map host-local endpoints to `host.docker.internal` when the API is running in Docker, disable proxy environment inheritance for those calls, and production/development Compose assets provide the required host-gateway mapping.
- Fixed the application shell so the root viewport no longer body-scrolls the whole UI. The top header and left navigation remain fixed while the menu and main content scroll independently.
- Verification passed: `PYTHONPATH=apps/api pytest -q apps/api/tests/test_llm_transport_retry.py apps/api/tests/test_react_engine_v11.py tools/gap_deploy_runner/tests/test_production_caddy_assets.py tools/gap_deploy_runner/tests/test_staging_runner_assets.py tools/gap_deploy_runner/tests/test_staging_caddy_assets.py` — 27 passed; `npm --prefix apps/web run build` passed; `git diff --check` passed; `openspec validate release-agent-lightweight-deploy --strict` passed.
- Prepared release version `v1.0.19` for a fresh tag and continuation of task 5.4 production evidence. The 5.4 checkbox remains unchecked until the fresh release delivery, public domain acceptance and administrator rollback evidence are complete.

## 2026-09-12 — v1.0.19 released and production Runner deployment verified; Hook still blocked by public HTTPS reset

- Published commit `948cd72` and tag `v1.0.19`. GitHub Release run `34676816422` built and pushed both immutable API/Web multi-arch images and uploaded the manifest; the only failed step was `Deliver signed manifest to GAP Hook`. Unauthenticated log download returned GitHub 403, but job-step evidence isolated the failure to Hook delivery after image and manifest success.
- Verified ACR image indexes for `v1.0.19`: API digest `sha256:36dc94043650296fa0050acf96949af28f8a445aa3d9a09355c6ca6d70d36d3e`; Web digest `sha256:8eaa569752e1121a86464e0a42a9a893a679ccbb2bf6c49d8f207de9fe60cc2f`.
- Because GitHub-to-public-Hook delivery remained blocked, used the authorized production host's fixed Runner manifest path to deploy the exact `v1.0.19` digest manifest. Runner returned `phase=succeeded`, `last_known_healthy.release_id=v1.0.19-948cd72`, and `pending_callbacks=[]`.
- Found that the production host's installed Runner compose asset predated the new `host.docker.internal:host-gateway` entry. Backed up the installed compose file under `/var/backups/gap-runner/`, added the missing extra host, and re-applied the same `v1.0.19` manifest through Runner. Final verification showed API container `ExtraHosts=["gap-runner.internal:host-gateway","host.docker.internal:host-gateway"]`, `getent hosts host.docker.internal -> 172.17.0.1`, API `/health` `{"status":"ok","version":"v1.0.19"}`, Web `/health` `{"status":"ok","version":"v1.0.19"}`, and host-local Caddy SNI `https://gapclaw.online/health` `{"status":"ok","version":"v1.0.19"}`.
- Public direct HTTP now reaches Caddy and returns `308 Location: https://gapclaw.online/health`; public direct HTTPS still resets during TLS (`curl: (35) Recv failure: Connection reset by peer`). This still blocks GitHub Hook proof and browser HTTPS acceptance. The 5.4 checkbox remains unchecked; rollback evidence is also still pending explicit administrator exercise.

## 2026-09-12 — qwen3:8b Ollama local model diagnosis and v1.0.20 fix prepared

- Diagnosed the remaining `qwen3:8b` failure on the production host. Ollama is installed and has `qwen3:8b`, but it listens on `127.0.0.1:11434` and `172.18.0.1:11434`; the API container's `host.docker.internal` resolves to `172.17.0.1`, so the prior default path refused connections. Added `GAP_LOCAL_MODEL_HOST=172.18.0.1` to `/opt/gap/.env` with a backup under `/var/backups/gap-runner/`, then re-applied `v1.0.19` through Runner. The API container now receives `GAP_LOCAL_MODEL_HOST=172.18.0.1`.
- Verified the API container can reach Ollama at `172.18.0.1:11434`; the OpenAI-compatible `/v1/chat/completions` path returns HTTP 200 but only populates qwen3's `reasoning` field and leaves `content` empty. Ollama native `/api/chat` with `think:false` returns normal content (`你好！`).
- Implemented a focused code fix for `provider=ollama`: normalize common Ollama base URLs, call native `/api/chat`, pass `think:false`, map native responses back into the existing OpenAI-shaped pipeline, allow Ollama resources to be created with a default placeholder key, and add a UI “Ollama 本地” preset for `qwen3:8b`.
- Verification passed: `PYTHONPATH=apps/api pytest -q apps/api/tests/test_llm_transport_retry.py apps/api/tests/test_llm_native_tools.py apps/api/tests/test_react_engine_v11.py` — 36 passed; `npm --prefix apps/web run build` passed; `git diff --check` passed. Prepared release version `v1.0.20`; task 5.4 remains unchecked until public Hook, browser HTTPS, and rollback evidence are complete.
- Published commit `fe1ef08` and tag `v1.0.20`. GitHub Release run `34680039163` built and pushed the immutable API/Web images, then failed only at the public Hook delivery step as expected while public HTTPS remains reset. Verified ACR image indexes: API `sha256:8d9ed117c418c6b3f007ae1b3857ee4cf415885ce747dcb0360b8ad14c3c52be`, Web `sha256:eed7c33901ef250f0d96411d3f9a4ad38fd7de91e2d69fee9e1f789ba9a32d4e`.
- Deployed the exact `v1.0.20` digest manifest through the authorized production Runner. Runner returned `phase=succeeded`, `last_known_healthy.release_id=v1.0.20-fe1ef08`, and `pending_callbacks=[]`.
- Production application-level verification from inside `gap-production-api-1` loaded LLM resource `7a179f79` (`provider=ollama`, `base_url=http://127.0.0.1:11434/v1`, `model=qwen3:8b`) and `test_llm_chat(...)` returned `你好！`. This closes the qwen3 local-model connectivity/content issue; task 5.4 still remains unchecked for the unrelated public Hook/HTTPS/rollback evidence.

## 2026-09-12 — stdio MCP 得到大脑 failure diagnosed and v1.0.21 fix prepared

- Diagnosed production `stdio MCP 失败: [Errno 2] No such file or directory` for MCP `得到大脑`. The production MCP row uses `protocol=stdio`, `command=npx`, `command_args=["-y","@getnote/mcp"]`; the current API container has Python only and no `node`/`npx` in PATH, so the stdio subprocess cannot start. The host installation is irrelevant because stdio MCP starts inside `gap-production-api-1`.
- Added Node/npm to the API image so stdio MCP servers that run via `npx` are available in production. Added a preflight command lookup in `_StdioSession.start()` so future missing-command failures say exactly which command is absent and that it must exist in the API container PATH.
- Verification passed: `PYTHONPATH=apps/api pytest -q apps/api/tests/test_api_dockerfile.py apps/api/tests/test_mcp_session_manager.py apps/api/tests/test_mcp_catalog.py` — 15 passed; `git diff --check` passed. Prepared release version `v1.0.21`; task 5.4 remains unchecked for the unrelated public Hook/HTTPS/rollback evidence.

## 2026-09-12 — v1.0.21 deployed and stdio MCP 得到大脑 verified

- Published commit `eac37d1` and tag `v1.0.21`. GitHub Release produced immutable image indexes: API `sha256:05795f14620f5b7d3f4b217e57d794a994d1c2ee8eb21d8fb65963091c9a0bd7`; Web `sha256:004eda28714f09c16cc87d3071ee866e910cab80b44db633ad9f00e96351fa58`. As before, the public Hook delivery path remains blocked by external `gapclaw.online` HTTPS reachability, so deployment used the authorized production Runner digest-manifest path.
- Deployed the exact `v1.0.21` digest manifest through the production Runner. Runner returned `phase=succeeded`, `last_known_healthy.release_id=v1.0.21-eac37d1`, and `pending_callbacks=[]`.
- Verified inside `gap-production-api-1` that `node`, `npm` and `npx` are now present in container PATH (`/usr/bin/node`, `/usr/bin/npm`, `/usr/bin/npx`). Production MCP connection verification for `得到大脑` returned 38 tools, including `list_notes`, `get_note`, `get_note_original`, `get_note_transcript`, `get_note_attachments`, `get_note_timeline`, `get_note_quick_note`, `get_note_todos`, `save_note` and `get_note_task_progress`; the original `[Errno 2] No such file or directory` failure is resolved.
- Task 5.4 remains unchecked only for the existing public Hook/HTTPS/browser and administrator rollback evidence gaps recorded in `findings.md`; the stdio MCP runtime issue is not a remaining blocker.

## 2026-09-14 — opsx:verify result

- OpenSpec status/instructions report `release-agent-lightweight-deploy` as schema `spec-driven` with 22/23 tasks complete. The only remaining checkbox is task 5.4, the controlled production CI Hook release and administrator rollback evidence.
- `openspec validate release-agent-lightweight-deploy --strict` passed.
- Focused release verification passed: `PYTHONPATH=apps/api pytest -q apps/api/tests/test_release_hook.py apps/api/tests/test_release_hook_end_to_end_contract.py apps/api/tests/test_release_runner_protocol.py apps/api/tests/test_release_ledger.py tools/gap_deploy_runner/tests/test_release_build_workflow.py tools/gap_deploy_runner/tests/test_production_caddy_assets.py tools/gap_deploy_runner/tests/test_runner_installation.py` — 43 passed. `npm --prefix apps/web run build` passed with existing Vite chunk/PURE-comment warnings. `git diff --check` passed.
- Public domain verification remains blocked: `http://gapclaw.online/health` reaches Caddy and returns `308 Location: https://gapclaw.online/health`, but `https://gapclaw.online/health` still fails with `curl: (35) Recv failure: Connection reset by peer`. This prevents external GitHub Hook proof and browser HTTPS acceptance, so task 5.4 remains unchecked and the change is not ready to archive.

## 2026-09-15 — v1.0.22 GitHub-to-Release-Agent revalidation

- Published commit `30ea7f0` and tag `v1.0.22`. GitHub Release run `34917597143` successfully built/pushed API and Web multi-architecture images, generated the immutable manifest and uploaded artifact `10376884675`.
- Manifest digests were API `sha256:db17ca8e5cd28dd7aefc8fa545c722f31c670512ad7b34950f20b0d37fd43515` and Web `sha256:f7f3e2f72dba3af51b57a0cb808f084b84d9d7f51d32b479d2eede3d7283abe4`.
- The signed Hook step alone failed after three `curl: (35) Recv failure: Connection reset by peer` attempts to `https://gapclaw.online/internal/release-hook`; no HTTP response was received.
- Host evidence remained healthy: Caddy active/listening on 80/443, configuration valid, host-local SNI `/health` returned HTTP 200, UFW inactive and INPUT accept. The main Hook route is not under callback mTLS.
- GAP API/release ledger and Runner contain no `v1.0.22-30ea7f0` intake or transition. Production remained healthy at `v1.0.21-eac37d1`, so no partial deploy or automatic rollback occurred.
- This is the same public DNSPod/provider reachability blocker already recorded for task 5.4. The checkbox remains unchecked. After ICP/provider access is cleared, rerun failed jobs for run `34917597143` and complete signature/replay, terminal callback, independent health and administrator rollback evidence.
