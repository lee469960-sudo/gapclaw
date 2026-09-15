# Findings: release-agent-lightweight-deploy

## Planning Setup

- The formal change lives at `openspec/changes/release-agent-lightweight-deploy/` and its `tasks.md` has 22 unchecked OpenSpec tasks (1.1–5.4).
- A previous Planning with Files session exists for the archived `model-router-role-groups` change. This change uses its own planning directory and must not inherit that change's completion state.
- Initial context-recovery command could not use the expected user-level skill path because that path is absent. The repository-local skill installation is available; no implementation state was lost because this change was just proposed.

## Constraints To Preserve

- Production deployment consumes digest-pinned API/Web manifest entries and must not execute tag-owned workflows, scripts, or Compose assets.
- The host Deploy Runner is the fixed deployment executor. GAP Release Agent is a deterministic management surface with limited status, health, and known-healthy rollback capability.
- Existing Code Agent Docker runtime is outside this change; Release Agent must not obtain a Docker, shell, SSH, MCP, or generic command bridge through it.

## OpenSpec Artifact Review

- `proposal.md` confirms two new capabilities only: `trusted-gap-deployment` and `release-management`; no existing main capability is modified.
- `design.md` fixes the implementation architecture: tag build emits a digest-based manifest; a protected default-branch workflow downloads it without tag checkout; the local-only Runner deploy command uses host-fixed Compose assets; the Runner mTLS surface is limited to status, health and known-healthy rollback.
- `specs/trusted-gap-deployment/spec.md` makes health success the conjunction of Compose service health and API `/health` returning `{"status":"ok"}`. Runner local state is independent of GAP availability, retains five successful releases, and retries fixed result callbacks.
- `specs/release-management/spec.md` makes Release Agent deterministic and read-oriented. It may explain records but never becomes a general tool executor. Rollback is administrator-only, requires displayed-target plus confirmation phrase, and cannot accept arbitrary tag/digest inputs.
- `tasks.md` contains 22 unchecked formal tasks. Their execution ordering is already reflected in `task_plan.md`: workflow/manifest boundary → Runner → GAP control plane → UI → verification and controlled rollout.

## Apply Context

- OpenSpec apply selected `release-agent-lightweight-deploy` with the `spec-driven` schema. All four planning artifacts are complete; implementation progress is 0/22.
- Task 1.1 is the first executable boundary. It must create strict manifest validation before workflow or Runner implementation so later components share one trustworthy input contract.

## Task 1.1 Discovery

- The repository has no standalone deploy-runner package yet. Existing deployment assets are under `deploy/` and the API test suite is under `apps/api/tests/`.
- The release manifest must be consumed by both a future host-side Runner and GitHub workflow logic, so it will use a tiny standard-library-only module under `deploy/` rather than importing the API application or database layer.
- Existing deployment configuration tests statically read real deployment assets. The new manifest tests can likewise run from repository root without a Docker daemon or production credentials.
- `deploy/` is currently read-only (`dr-xr-xr-x`), so it cannot contain new repository source in this workspace. This is recorded as an installation/asset-management constraint; task 1.1 will use a writable `tools/` source package and later tasks will install fixed artifacts to the host-managed location.
- The resulting `tools.gap_deploy_runner.release_manifest` module uses only the standard library and exposes a frozen manifest model plus stable validation reasons. It accepts only an exact schema, semver-like `vMAJOR.MINOR.PATCH` tag/version equality, lowercase 40-character commit SHA, restricted target id, timezone-aware timestamp, allowed API/Web repository plus SHA-256 digest references, and no mutable image tags.

## Task 1.2 Discovery and Outcome

- The tag build workflow now validates `GITHUB_REF_NAME` against `deploy/gap.version` before it logs in to ACR or builds images. This closes the prior gap where a semver-shaped but different tag could trigger a deployment package.
- API and Web build steps have stable ids and their `build-push-action` digest outputs are written as canonical `repository@sha256:...` entries in `.release/release-manifest.json`.
- The manifest is uploaded as the short-retained `gap-release-manifest` artifact for a later default-branch production workflow. Task 1.2 intentionally leaves the legacy deploy job in place; task 1.4 removes it after task 1.3 provides the replacement.
- Focused manifest/workflow tests passed. Ruby could parse the workflow YAML but emitted a local world-writable-PATH warning; this does not change the test result and is recorded in the plan.

## Task 1.3 Research

- GitHub documents that `workflow_run` only triggers when its workflow file exists on the default branch. This provides the desired separation from tag-owned deployment YAML.
- `actions/download-artifact` supports a different source run when supplied `run-id` and a token with `actions: read`; the deployment workflow will use `github.event.workflow_run.id` and `github.token` under explicit `actions: read` permission.
- The production workflow must not call `actions/checkout`: it needs only the downloaded artifact and `/opt/gap-runner/bin/gap-deploy-runner`, ensuring it cannot execute tag-owned scripts or Compose files.
- Added `.github/workflows/deploy-production.yml`. Its `workflow_run` gate accepts only successful push-based Release runs from the same repository with a tag-shaped head branch, declares `actions: read`, uses the GitHub `production` Environment, downloads `gap-release-manifest` by source run id, and invokes only the fixed host binary.
- Static tests verify the new workflow has no checkout, `scripts/deploy.sh`, production Compose path, or ACR password reference. Both release workflow YAML files parse successfully with Python/PyYAML.

## Task 1.4 Outcome

- The tag build workflow no longer has a `deploy` job, job-level image outputs used only by that deploy job, a self-hosted production runner label, tag checkout for deployment, or the prior ACR credential environment transfer.
- ACR credentials remain only in the hosted build job's `docker/login-action` configuration; this is necessary to push images and is distinct from giving production deployment access to registry secrets.
- Phase 1 is complete. The next phase begins with the Runner's fixed, local CLI boundary before adding Compose execution, state persistence or HTTP control endpoints.

## Task 2.1 Outcome

- The new `tools.gap_deploy_runner` package defines the fixed operation vocabulary (`deploy`, `status`, `health`, `rollback`), a frozen release-state shape and stable rejection reasons. It has no subprocess, shell, Docker, HTTP or MCP bridge.
- `deploy` accepts only a strict manifest already validated against a fixed target and allowed API/Web repositories. `status` and `health` expose only fixed state. `rollback` accepts no target/tag/digest input and reports no healthy baseline until persistence is added in task 2.3.
- The CLI parser exposes only the four fixed subcommands. Its only deploy argument is `--manifest`; arbitrary `--command` input is rejected by argument parsing.

## Task 2.2 Outcome

- Fixed Runner assets now live in the writable `tools/gap_deploy_runner/assets/` source package and are designed for installation under `/opt/gap-runner/`; no production deployment depends on a tag checkout or the read-only repository `deploy/` directory.
- `RunnerConfig` reads only an explicit host env file, requires pull-only ACR configuration, derives or accepts the fixed API/Web repositories, rejects `GITHUB_*` keys, and injects only digest-pinned release image values for Compose use.
- The source Compose asset preserves the existing Code Agent socket mount as an explicit out-of-scope runtime boundary, while API/Web image references use only `GAP_RELEASE_*_IMAGE` values and never `latest`.

## Task 2.3 Outcome

- `ReleaseStateStore` persists state atomically through a sibling temporary file, records a last-known-healthy manifest and retains only the latest five successful manifests.
- A target-state-file scoped lock serializes concurrent threads. On restart/load, an interrupted active phase is explicitly converted to `reconciliation_required`; malformed or wrong-target state also fails closed to that phase.

## Task 2.5 Outcome

- The Runner network surface has exactly three fixed routes: `GET /v1/status`, `GET /v1/health`, and `POST /v1/rollback`. The `deploy` route does not exist on the network API and deploy remains available only through the local CLI.
- `MtlsFiles.server_context()` loads the Runner certificate/key and trusted CA, then sets `CERT_REQUIRED`; a client without a certificate cannot complete the local TLS exchange.
- The integration test creates an ephemeral constrained CA and separately signed Runner/GAP-client identities. It proves a trusted client receives the fixed status response while a client lacking a certificate is rejected before the request is handled.
- The restricted rollback boundary remains input-free: an URL carrying tag or digest-like query data does not match the endpoint, and the command-boundary test rejects a caller-supplied rollback target.

## Task 2.6 Outcome

- The Runner source now contains a fixed host layout model: `/opt/gap-runner` owns the Compose, state and TLS paths, while `/opt/gap/.env` remains the host-only application/ACR configuration path.
- Installation validation uses only the fixed `docker compose version` probe and fixed expected paths. It fails closed when the local env, Compose asset, CA, Runner certificate, private key or state directory is absent.
- `/opt/gap/.env` and the Runner private key must not be group/world-readable; the state directory must not be group/world-writable. The systemd unit runs as `gap-runner`, limits writable paths to Runner state, requires the Docker service, and uses `NoNewPrivileges`, `ProtectHome`, `ProtectSystem` and a restrictive umask.
- The initialization script deliberately creates only the fixed directories, ownership/modes and static assets. It does not create credentials or certificates, which must be provisioned separately before the service is enabled.

## Task 3.1 Discovery

- GAP API models are centralized in `apps/api/app/models.py`. New tables are created by `Base.metadata.create_all`, while startup compatibility code in `apps/api/app/startup.py` adds missing columns to existing tables; the project does not use an Alembic migration tree.
- Release data must be modeled in dedicated release tables rather than `CodeControlAudit`: task 3.1 requires its own manifest, lifecycle audit, rollback-request and callback-delivery records, and the Release Agent must not share the Code Agent control path.
- API tests conventionally use `create_engine("sqlite:///:memory:")`, `Base.metadata.create_all(engine)` and a scoped session. This provides the database-level evidence needed for idempotent terminal callbacks, redaction and sorted history without a running application or Runner host.

## Task 3.1 Outcome

- Added dedicated manifest, lifecycle-audit, rollback-request and callback-delivery SQLAlchemy models. Their new tables are created by the project's existing `Base.metadata.create_all` startup migration path, independently of Code Agent tables.
- `ReleaseLedger` accepts only fixed release fields, rejects sensitive field names before persistence, returns an existing terminal audit on repeat `(release_id, status)` delivery, and stores one matching delivery record.
- History is queried by target and ordered by event time descending. Rollback requests persist the displayed release/target and requesting operator for later authorization and Runner invocation work.

## Task 3.2 Blocker Discovery

- The current production Compose exposes the API directly on port 8000 over HTTP. Repository deployment assets contain no Nginx, Caddy, Traefik or other TLS termination configuration.
- A FastAPI route cannot independently authenticate the peer TLS client certificate after TLS has already terminated elsewhere. Trusting a client-certificate header without a fixed trusted terminator and private network boundary would let callers forge Runner identity.
- Completing task 3.2 therefore requires an explicit deployment decision: add a host-managed mTLS reverse proxy/terminator that exposes only the fixed Runner callback path and forwards an authenticated identity to the API, or operate the API server itself with mTLS. The former is the recommended lightweight topology; it is not currently represented in the approved implementation assets.

## Nginx Ingress Decision

- User confirmed that Nginx belongs in this OpenSpec change and provided `gapclaw.online` as the primary domain.
- The revised OpenSpec introduces task 2.7 as a security prerequisite for 3.2: Nginx owns 80/443, `gapclaw.online` routes GAP traffic, and `runner.gapclaw.online` exposes only the fixed mTLS callback.
- A future second Compose stack is intentionally not given a placeholder route; it will join the shared `gap-edge` network only with a separately declared hostname, avoiding a catch-all route or port conflict.

## Task 2.7 Outcome

- `gap-edge.compose.yml` owns public 80/443 and attaches only to the external `gap-edge` network. GAP API and Web attach via stable `gap-api` and `gap-web` aliases; API health remains bound to `127.0.0.1`, and Web has no host port binding.
- Nginx explicitly routes `gapclaw.online` browser/API/WebSocket traffic, rejects unknown HTTP/HTTPS hosts, and exposes only `POST /internal/release-runner/callback` on `runner.gapclaw.online` with `ssl_verify_client on` and the Runner CA.
- Nginx replaces any caller-supplied Runner verification header with its own TLS verification result before forwarding. A later Compose can use `gap-edge` only through a newly declared hostname; no default proxy target exists.

## Task 2.7 Security Audit Reopened

- The first edge design attached GAP API to the shared `gap-edge` network. That would let a future Compose participant contact `gap-api` and forge the Nginx verification header, so 2.7 was reopened before starting 3.2.
- The corrected topology has `gap-edge` for Nginx/Web and future explicit stacks, and an external `gap-proxy` network for Nginx/API only. GAP API no longer joins the shared network; Nginx remains the sole route to the fixed callback handler.

## Task 3.2 Progress

- Added a fixed GAP-side Runner protocol boundary. The client accepts only `https://runner.gapclaw.online` and configures CA verification plus GAP client certificate/key for the three fixed Runner operations.
- The callback service accepts only Nginx's verified-client result and delegates fixed event persistence to `ReleaseLedger`; it neither accepts a URL nor a command field.
- Remaining 3.2 work is the actual FastAPI fixed callback route plus persisted delivery retry and Runner/GAP status reconciliation.

## Task 1.1 Discovery

- The repository has no standalone deploy-runner package yet. Existing deployment assets are under `deploy/` and the API test suite is under `apps/api/tests/`.
- The release manifest must be consumed by both a future host-side Runner and GitHub workflow logic, so it will use a tiny standard-library-only module under `deploy/` rather than importing the API application or database layer.
- Existing deployment configuration tests statically read real deployment assets. The new manifest tests can likewise run from repository root without a Docker daemon or production credentials.

## Open Questions

- None recorded during initialization. The OpenSpec design resolves implementation-shaping decisions; host-specific certificate values and production credentials remain rollout configuration, not product-scope questions.

## Task 3.2 Outcome

- The callback route receives the complete fixed manifest plus terminal result envelope, so GAP can persist the immutable API/Web digest fields before creating its idempotent lifecycle audit. The proxy verification header is accepted only when it equals `SUCCESS`; the private `gap-proxy` topology established in task 2.7 is the condition that makes this header authoritative.
- GAP's Runner client permits only the fixed Runner host and three fixed paths. Runner's callback transport permits only the fixed `runner.gapclaw.online` callback URL and configures the Runner client identity plus CA validation; neither side provides a generic HTTP, shell or command bridge.
- Callback entries are durable local state with fixed fields, attempts and next-attempt time. A failed terminal delivery remains after restart and is retried with bounded exponential backoff on the next fixed Runner mTLS access. GAP's reconciler returns an explicit `reconciliation_required` state for malformed status, an active phase, or a missing matching terminal audit.

## Task 3.3 Outcome

- The correct system-agent boundary is a static deterministic definition and service, not a row in the configurable `Agent` table. A configurable row would enter the normal chat/ReAct runtime and contradict the requirement that Release Agent cannot acquire generic tools.
- The read-only release-management endpoints authenticate through the existing session dependency but expose only GET methods. Their source is `ReleaseLedger`'s persisted audit history; no Runner URL, client key, configuration value or transport call is accepted from the API request.
- Task 3.4 needs a controlled source for the Runner address, CA and client-certificate references. Therefore task 3.5 is implemented next as a dependency-first execution order; `tasks.md` remains the sole formal checklist and is not reordered.

## Task 3.5 Outcome

- Release Management configuration is process-controlled through `Settings`, not supplied by browser/API callers and not stored in release tables. It has one allowed Runner URL and validates target syntax, absolute mTLS references and a bounded timeout before producing `ReleaseRunnerTls`.
- The config endpoint deliberately uses a non-executable preview load: it can report a missing/invalid host setup as unavailable, but only the validating load can construct an mTLS client. Its public view contains no certificate file reference, private key, ACR credential or application secret.

## Task 3.4 Outcome

- The Runner status protocol now supplies a deliberately narrow `last_known_healthy` object from persistent state: only `release_id` and fixed `target_id`. This is sufficient to display and confirm the rollback target while keeping images, digests and commands out of the rollback request.
- GAP obtains the baseline before it creates a rollback audit request. Any missing baseline, changed target, non-admin caller or incorrect confirmation fails before `ReleaseRunnerClient.rollback()` can run. A valid request records the operator and is marked `submitted`; a Runner transport failure is recorded as `failed`.

## Task 4.1 Outcome

- The existing Vue application has no component-test runner installed. The task uses a source-level view contract test together with the production Vite build: the contract asserts the status/history calls and all required state/digest fields, while Vite verifies the `.vue` component compiles into the release bundle.
- `page_release_management.cgi` is included in the backend page map/menu and the front-end role-permission editor, so custom roles can be granted the view explicitly. The view itself is read-only; rollback controls remain for task 4.2.

## Task 4.2 Outcome

- The router already resolves the authenticated shell before entering the release-management view. The UI can therefore derive the administrator context from the cached `master`/`admin` roles and avoids a separate role-discovery endpoint.
- Only administrators request and see the Runner's `rollback-target`. The dialog displays that immutable `release_id`/`target_id` pair, disables submission until the exact `ROLLBACK` phrase is entered, and sends only those displayed values to the existing constrained API; no input permits an image, digest or arbitrary version.
- The project has no Vue component-test runner. The focused source-level view contract now asserts the admin guard, fixed endpoints, exact confirmation guard and displayed-value submission, while the production Vite build verifies the component compiles.

## Task 4.3 Outcome

- The release view does not call the configuration preview endpoint and has no data binding for CA files, client keys, certificate references, ACR credentials or application secrets. Its status/history response model is limited to archived release fields already validated by the API.
- A missing healthy baseline is displayed distinctly. Other unavailable baseline responses are normalized to an actionable Runner-unavailable prompt that recommends refresh and reconciliation without rendering the raw transport/configuration detail or claiming a successful release.

## Task 5.1 Blocking Topology Gap

- **User requirement to verify:** GAP must use mutually authenticated Runner `status`/`health`/restricted `rollback`, while `runner.gapclaw.online` remains the fixed mTLS callback ingress.
- **Evidence still missing:** the current Nginx vhost for `runner.gapclaw.online` proxies only `/internal/release-runner/callback` to GAP and rejects every other path; the Runner listener example binds `127.0.0.1:9443`. GAP's configured client URL is nevertheless `https://runner.gapclaw.online`, so its `/v1/*` calls have no route to the host Runner.
- **Specific unexecuted tool action:** run an isolated edge/Runner mTLS contract test that requests `https://runner.gapclaw.online/v1/status` through the declared topology.
- **Expected decision condition:** the request must reach the fixed Runner and return its authenticated status without exposing a public generic endpoint. Under the current assets it cannot satisfy that condition, so documentation must not claim the control loop is deployable until the topology is changed.

## Private Runner Topology Decision

- User authorized the recommended separation: `runner.gapclaw.online` remains the Nginx mTLS callback endpoint, while GAP-to-Runner control calls move to `https://gap-runner.internal:9443`.
- The private name resolves only for the GAP API container through Docker's host-gateway. The Runner must bind its configured Docker-bridge address and the host firewall must allow port 9443 only from that bridge; its certificate must include `gap-runner.internal`.
- This changes the approved Runner address contract, so completed task 3.5 is reopened before documentation proceeds. The earlier task 5.1 blocker is thereby resolved at the design level but still awaits implementation evidence.

## Reopened Task 3.5 Outcome

- `ReleaseRunnerTls` now fails closed unless the configured URL is exactly `https://gap-runner.internal:9443`; the former public callback host and arbitrary URLs are rejected. The redacted configuration API still exposes no mTLS reference path or secret.
- The fixed production Compose asset gives only the API service a `gap-runner.internal:host-gateway` mapping and mounts `/opt/gap/release-mtls` read-only at `/run/gap-release-mtls`. The public Nginx asset continues to expose only the callback and has no `/v1/*` control proxy.
- The Runner environment example documents a Docker-bridge listener, with host firewall restriction as an installation requirement. The remaining task 5.1 documentation can now state an actually routable private-control and public-callback topology.

## Task 5.1 Outcome

- `docs/deployment.md` now replaces the obsolete tag-checkout deployment instructions with the protected manifest workflow, fixed host assets, private Runner control URL, callback-only public host, host-only ACR pull credentials and certificate layout.
- It documents first healthy-baseline behavior, mTLS overlap rotation, administrator-confirmed and emergency fixed rollback, and the explicit non-escalation of existing Code Agent Docker privileges.
- A source-level documentation contract test ties its private/public URLs and fixed asset commands to the shipped assets, preventing reintroduction of the old tag-script or `git pull` deployment procedure.

## Task 5.2 Outcome

- The cross-layer contract uses digest-pinned manifests, a durable Runner state store and a real GAP release ledger against a local SQLite test database; it never contacts ACR, GitHub or production infrastructure.
- It proves a healthy deployment stores the API digest in GAP, a failed health check re-applies the healthy baseline and persists `rolled_back`, a callback remains durable while GAP is unavailable, and a restarted callback dispatcher delivers it so GAP reconciliation becomes synchronized.
- The test revealed that terminal health-failure outcomes were not persisted before callback/restart. `ReleaseStateStore.record_terminal` now persists only allowed terminal phases, and deployment invokes it before emitting the terminal callback.

## Task 5.3 Outcome

- The full API suite passed with 1,153 tests and four existing skips. Runner's full suite passed with 53 tests after the local-only mTLS integration was run with approved loopback-binding permission. The Web production build passed with existing Vite chunk-size/PURE-comment warnings.
- Rendering the real production Compose correctly failed closed because this workspace does not have the host-owned `/opt/gap/.env`; no secret file was created. A temporary non-secret copy differing only in its `env_file` location rendered successfully with digest inputs, private API host-gateway mapping and declared networks, then was removed.
- The edge Compose rendered successfully. `git diff --check` and OpenSpec strict validation passed.

## Task 5.4 Environment Gap

- **User requirement to verify:** complete one controlled non-production release exercise proving the protected workflow does not execute tag content or expose ACR pull credentials, Runner status/health is independent, and confirmed rollback completes its audit loop.
- **Evidence still missing:** repository configuration contains no non-production GAP deployment target, and the read-only local Docker inventory contains no GAP API/Web/Deploy Runner stack or staging Runner. Existing containers are unrelated services and are out of scope.
- **Specific unexecuted tool action:** run the approved non-production release manifest through a provisioned `staging` protected Environment and its dedicated fixed Deploy Runner, then use the staging Release Management UI to perform the exact confirmed rollback.
- **Expected decision condition:** the staging Runner must deploy only digest-pinned manifest images, report independent status/health, retry a deliberately interrupted callback, and record the administrator-confirmed rollback without accepting a tag, digest or arbitrary command. No such authorized target has been provided, so this action is not executed.

## Production Manifest Gate

- **User requirement to verify:** the production host must deploy only the image digests emitted by the authorized `v1.0.10` GitHub Release build.
- **Evidence still missing:** commit `fa920e2` and tag `v1.0.10` are now pushed, but the build result, downloaded `gap-release-manifest`, and its successful image digest values have not yet been observed.
- **Specific unexecuted tool action:** query the tag-triggered GitHub Actions run and, only after success, retrieve its `gap-release-manifest` artifact for fixed Runner validation.
- **Expected decision condition:** the run must conclude successfully and the manifest must pass the Runner's exact tag, target, approved-repository and immutable-digest checks; otherwise host deployment stops with the existing site unchanged.

## Production ACR Credential Gap

- **User requirement to verify:** the production Deploy Runner must pull only the release manifest's ACR images using a host-only pull credential, without putting credentials in GitHub workflow inputs or public configuration.
- **Evidence still missing:** the successful `v1.0.10` manifest names the authorized ACR API/Web repositories, but the supplied registry authentication returns HTTP 401; the local deployment environment does not contain a usable ACR username/password, and Docker Hub is unreachable from the host for the database image fallback.
- **Specific unexecuted tool action:** authenticate the Deploy Runner account to the stated ACR registry with a newly supplied AcrPull credential, then run `docker manifest inspect` for the selected ACR database image and the manifest API/Web digests.
- **Expected decision condition:** all three immutable images must be retrievable with that host-only credential before `/opt/gap/.env` is created, the Runner service is enabled, or the GitHub `production` runner is registered; an authentication or manifest failure leaves the current `gapclaw.online` site unchanged.

## Production ACR Credential Resolution

- The registry's HTTP 401 came from treating its Bearer-token API as basic-auth catalog access, not from the supplied Docker credential. Standard `docker login --password-stdin` as the restricted `gap-runner` account succeeded and the manifest API digest is readable.
- The existing ACR database repository contained only an arm64 PostgreSQL image, while the host is amd64 and Docker Hub timed out. A linux/amd64 upstream pgvector image was therefore published to the authorized ACR namespace and the resulting ACR digest is the host's fixed database input.
- The Deploy Runner now has the required Docker-group access and its credential store is confined to `/opt/gap-runner/.docker`; credentials remain absent from the GitHub deployment workflow, app images, audit records and public Caddy configuration.

## GitHub Runner Registration Authority Gap

- **User requirement to verify:** the protected production deployment must execute only on the authorized host through a repository self-hosted runner carrying the `production` label.
- **Evidence still missing:** GitHub has queued the protected production workflow. The supplied PAT receives HTTP 403 when requesting a repository runner registration token, and the supplied one-time token receives HTTP 404 from the runner-registration endpoint; no GitHub Runner can therefore be registered or prove it is the job's executor.
- **Specific unexecuted tool action:** submit a newly generated, unexpired one-time runner registration token from this exact repository (or a newly issued repository-administration credential) to configure `/opt/actions-runner` with the fixed `production` label and start its service.
- **Expected decision condition:** GitHub must show the runner online with the required label and the existing queued production workflow must start on that runner; any registration error leaves the runner unconfigured and the current Caddy site unchanged.

## Production Caddy Migration

- Production Nginx and its shared `gap-edge`/`gap-proxy` model were removed. Host-installed Caddy now owns the production sites, and API/Web are only loopback upstreams. This prevents a future Compose stack from joining a shared proxy network and reaching GAP API directly.
- The production installer uses Caddy's validation-before-reload sequence and requires an explicit main-config import plus `/etc/caddy/production/runner-ca.crt`; it neither edits the main Caddyfile nor introduces a default hostname for a future stack.

## Production Caddy Runtime Gap

- **User requirement to verify:** the host Caddy service must load the reviewed production site, reject non-callback paths and unauthenticated callback clients, and route `gapclaw.online` only to the loopback GAP services.
- **Evidence still missing:** this local workspace has no `caddy` binary, host `/etc/caddy/Caddyfile`, production callback CA, DNS/TLS issuance, or authorized deployment server; local verification therefore proves the shipped assets and installer contract, not a running host configuration.
- **Specific unexecuted tool action:** on the authorized non-production host, run `sudo sh tools/gap_deploy_runner/assets/install-production-caddy.sh` followed by authenticated/unauthenticated HTTPS checks for both production hostnames and the callback path.
- **Expected decision condition:** Caddy validation and reload succeed; `gapclaw.online` serves only through loopback upstreams, callback `POST` succeeds only with the trusted client certificate, and other callback paths or unauthenticated clients are rejected. This host exercise is part of remaining task 5.4 and has not been run.

## 2026-09-12 — Authorized Production Host Initialization

- The user clarified that `124.221.215.177` is the intended production host, not a staging host, and explicitly authorized replacement of its temporary `gapclaw.online` Caddy proxy. The current Caddyfile also serves `light.gapclaw.online`; that site must remain untouched.
- Read-only preflight confirmed Ubuntu 24.04, sudo-capable `ubuntu`, Docker 29.7.2, Docker Compose v5.5.0, Caddy 2.6.2, `gapclaw.online` initialization health `{"ok":true,"status":"live"}`, and DNS for both `gapclaw.online` and `runner.gapclaw.online` at the production host.
- Caddy 2.6.2 is not compatible with the reviewed production mTLS `trust_pool file` configuration. The rollout must preserve the current config/binary, upgrade Caddy, add a reviewed site import, preserve `light.gapclaw.online`, then validate before reload.
- User-provided credentials are intentionally excluded from this record; they must not be emitted in command output or persisted in source and must be rotated after the rollout.

## 2026-09-12 — Production Rollout Manifest Blocker

- The authorized host's current `gapclaw.online` initializer proxies to an existing OpenClaw Compose project on `127.0.0.1:18789`; GAP ports 8000/8080 are free. User explicitly authorized eventual replacement of that initial proxy, while `light.gapclaw.online` must remain unchanged.
- Caddy was safely upgraded from 2.6.2 to 2.11.4 after a host backup at `/var/backups/gap-caddy/20260911T170845Z`; the existing Caddyfile validated and Caddy remained active. No GAP site, Runner, Compose, PKI or GitHub Runner was installed.
- The repository HEAD is already tagged `v1.0.9`, but the Release Agent implementation, production workflow and deploy assets are uncommitted local changes. Deploying them directly would bypass the required GitHub tag build and immutable manifest; it is not an acceptable substitute for the specified release chain.

## Immutable Manifest Gap

- **User requirement to verify:** production deployment must consume a GitHub-build-verified immutable API/Web digest manifest and must not execute local/tag-unverified deployment content.
- **Evidence still missing:** an authorized commit containing the reviewed Release Agent/workflow/asset changes, a pushed tag whose version matches `deploy/gap.version`, and the resulting successful GitHub release artifact.
- **Specific unexecuted tool action:** after user authorizes a scoped commit and push that excludes unrelated working-tree changes, push the next matching release tag and wait for the Release workflow to publish `gap-release-manifest`.
- **Expected decision condition:** the production deployment workflow downloads that exact successful-run artifact and invokes only the fixed host Runner; no local build, mutable tag or uncommitted source is used.

## 2026-09-12 — Task 5.4 Current Non-production Exercise Gap

- **User requirement to verify:** complete one controlled **non-production** CI Hook exercise that proves no ACR pull credential reaches CI/GAP audit, validates HMAC and replay denial, reports Runner-independent status/health, performs administrator-confirmed rollback and closes the callback audit loop.
- **Evidence still missing:** the only authorized host is the production `gapclaw.online` host. No isolated staging GAP/Runner/Caddy endpoint, staging Hook secret, staging DNS/certificate or non-production image set is available. The new production-only fixed Hook must not be used to substitute for this exercise.
- **Specific unexecuted tool action:** provision an isolated staging host and its dedicated Caddy/GAP/Runner mTLS assets, configure a staging-only Hook secret and endpoint, then trigger a tagged test release against that endpoint and execute the documented confirmed rollback.
- **Expected decision condition:** the staging Hook accepts exactly one fresh signed digest manifest, rejects its replay, Runner status/health remains available independently, the deliberate callback interruption retries successfully, and the confirmed rollback is recorded without accepting a user tag/digest/command. Until all observations exist, task 5.4 remains unchecked.

## 2026-09-12 — Task 5.4 Authorized Production Validation

- The preceding non-production gap is superseded by the user's explicit authorization to exercise the controlled rollout on the single production host. The formal 5.4 task and design migration plan were updated before proceeding; this record preserves the historical constraint without treating it as current scope.
- **User requirement to verify:** complete one controlled production CI Hook release with a recoverable Caddy cutover, health-gated digest deployment, HMAC/replay rejection, independent Runner status/health, administrator-confirmed rollback, and durable GAP audit.
- **Evidence still missing:** the current GitHub token can read workflow artifacts but returned HTTP 403 for an Actions write operation, so `GAP_RELEASE_HOOK_SECRET` cannot yet be set in repository Actions secrets; the new GAP Hook is not yet deployed on the host.
- **Specific unexecuted tool action:** after the host bootstrap succeeds, use a credential authorized to write repository Actions secrets to set the same host-provisioned `GAP_RELEASE_HOOK_SECRET`, then create one fresh matching tag and observe its signed Hook delivery through production.
- **Expected decision condition:** GitHub reports a 2xx Hook delivery; GAP accepts exactly one fresh canonical digest manifest, rejects its replay and invalid signature, the Runner records healthy status independently, the callback/audit becomes terminal, and the displayed administrator rollback is accepted only with the exact confirmation.

## 2026-09-12 — Production Bootstrap Findings

- The original GAP client certificate had CN `gap-release-api`, while the Runner allows only its fixed `gap-client` identity. The old CA private key was not retained. A new host-only private CA reissued the Runner and GAP client identities; mTLS `status` and `health` are now reachable through `gap-runner.internal:9443` without exposing key material.
- The Docker Compose v5 `--dry-run up` invocation reported simulated create/start steps. It did not leave GAP containers running, but its output is misleading for this version and must not be reused as a non-mutating validation method.
- Runner correctly refused the first v1.0.12 baseline because the GAP API failed startup. The root cause is an existing PostgreSQL schema mismatch: `code_project_manifests.status` was `VARCHAR(16)` although startup writes the longer `security_republish_required` status. Source now widens the model and performs a PostgreSQL-compatible type migration before that update; focused migration tests passed and the patch is released as v1.0.13.

## 2026-09-12 — Production Bootstrap Findings After Caddy Cutover

- Caddy validated and reloaded the reviewed production site while preserving `light.gapclaw.online`; GAP API/Web remain loopback upstreams. Host-local SNI validation proves `gapclaw.online` routes to the deployed GAP `v1.0.12` health endpoint, but public HTTP/HTTPS traffic for the same domain is intercepted or reset before a usable external health response is observed.
- Runner `v1.0.17` established a healthy baseline from the immutable `v1.0.12` manifest. The later `v1.0.18` asset change removes ACME dependency from `runner.gapclaw.online` by using a fixed release-CA-signed callback server certificate. The host reissued Runner's certificate with both `serverAuth` and `clientAuth` so its fixed callback dispatcher can authenticate to Caddy; pending callbacks were delivered and cleared.
- The local dispatcher retry was executed directly after discovering the mTLS and EKU issues. That closes the callback audit evidence for the bootstrapped manifest but does not replace a fresh GitHub CI Hook delivery, because public `gapclaw.online` remains unreachable from external clients.

## 2026-09-12 — Task 5.4 Public Domain Reachability Gap

- **User requirement to verify:** complete the controlled production CI Hook release through the real public `gapclaw.online` endpoint so GitHub can deliver the signed manifest and users can open the production domain.
- **Evidence still missing:** public `http://gapclaw.online/health` returns `302` to `https://dnspod.qcloud.com/static/webblock.html?d=gapclaw.online`, and public `https://gapclaw.online/health` resets during TLS ClientHello. The host-local Caddy route is healthy, but that does not prove external reachability.
- **Specific unexecuted tool action:** after the domain/provider block is cleared, run `curl --noproxy '*' --fail https://gapclaw.online/health` and a signed GitHub Hook delivery from outside the host to `https://gapclaw.online/internal/release-hook`.
- **Expected decision condition:** the public health check must return `{"status":"ok","version":"v1.0.12"}` or the newly deployed version, and the external Hook delivery must receive a 2xx from GAP without DNSPod/webblock redirects, TLS resets, or Caddy bypasses.

## 2026-09-12 — Task 5.4 GitHub Hook Secret Gap

- **User requirement to verify:** prove HMAC signature acceptance, invalid-signature denial and replay denial for the production GitHub CI Hook without exposing ACR pull credentials.
- **Evidence still missing:** the available GitHub credential previously returned HTTP 403 when attempting an Actions secret write, so the repository-side `GAP_RELEASE_HOOK_SECRET` cannot yet be set to match the host/GAP secret. No fresh tag can prove a valid GitHub-hosted signed delivery until this secret is configured.
- **Specific unexecuted tool action:** with a repository credential authorized to manage Actions secrets, set `GAP_RELEASE_HOOK_SECRET` to the production value and push one fresh version tag whose `deploy/gap.version` matches the tag.
- **Expected decision condition:** GitHub's Release workflow must build digest-pinned images, omit ACR pull credentials from the Hook step, deliver exactly one valid HMAC envelope to GAP, and GAP must reject a replay of the same delivery id and an invalid signature.

## 2026-09-12 — Task 5.4 Administrator Rollback Evidence Gap

- **User requirement to verify:** perform the administrator-confirmed rollback path and record the audit loop, proving arbitrary tag/digest/command input cannot trigger rollback.
- **Evidence still missing:** the production Runner has a last-known-healthy baseline and the callback/audit path works, but no authenticated administrator UI/API rollback has been executed against the displayed baseline after the public domain cutover issue.
- **Specific unexecuted tool action:** after an administrator session is available on the production domain or an equivalent authenticated API session is provided, call the Release Management rollback endpoint using only the displayed `release_id`, `target_id` and exact confirmation phrase.
- **Expected decision condition:** non-admin or malformed confirmation remains rejected, the valid administrator request invokes only Runner's fixed rollback operation, Runner status remains healthy, and GAP records the rollback audit without accepting a caller-supplied image, digest, tag or shell command.

## 2026-09-15 — Task 5.4 v1.0.22 Revalidation Gap

- **User requirement to verify:** deliver the `v1.0.22` immutable manifest from GitHub through the signed production Hook into Release Agent/Runner and observe terminal healthy audit evidence.
- **Evidence still missing:** run `34917597143` pushed both digest images and uploaded the manifest, but all three GitHub Hook attempts were reset during TLS before an HTTP response. GAP has no corresponding Hook delivery/audit and Runner remains at `v1.0.21`.
- **Specific unexecuted tool action:** after `gapclaw.online` ICP/provider blocking is cleared, invoke GitHub's rerun-failed-jobs action for run `34917597143` using an Actions-write credential or the repository UI.
- **Expected decision condition:** Hook step receives 2xx, GAP records exactly one accepted delivery for `v1.0.22-30ea7f0`, Runner reaches `phase=succeeded` with matching API/Web digests, callback audit becomes terminal, replay/invalid signature remain rejected, and the administrator-confirmed rollback evidence is completed before checking task 5.4.
