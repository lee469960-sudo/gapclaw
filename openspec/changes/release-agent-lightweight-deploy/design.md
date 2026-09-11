## Context

See [proposal.md](proposal.md) for motivation. The current `release.yml` is tag-triggered: it builds and pushes API/Web images, then a production self-hosted job checks out the tag and executes that tag's `scripts/deploy.sh`. The script loads the repository Compose file, receives ACR credentials from GitHub Actions, deploys tag-addressed images, and performs API health checks. It has no immutable release manifest, durable last-known-healthy state, automatic rollback, or GAP-side audit/control plane.

The target is one existing production host running GAP and, later, a second independent Compose stack. The host-installed Caddy service is the only public listener and routes each stack by a distinct domain or subdomain. The accepted trust level is deliberately lightweight: protected tags, protected default branch and GitHub Environment controls, digest-pinned deployment inputs, host-local pull-only registry credentials and private-CA mTLS at the Runner callback route. Image signing/provenance attestation, Kubernetes, blue/green releases, business smoke tests, general remote-host deployment, and Code Agent Docker-runtime redesign are excluded.

## Goals / Non-Goals

**Goals:**

- Turn the existing build-to-ACR flow into a GAP self-deployment loop with immutable release inputs, a fixed host executor, health-gated completion, automatic recovery, and durable audit.
- Make Release Agent a constrained system capability: status/explanation and administrator-confirmed rollback, never a general host automation Agent.
- Preserve operation when GAP is temporarily unavailable by making the host Runner independently stateful and observable.

**Non-Goals:**

- No image signing, OIDC/cosign adoption, arbitrary deploy commands, arbitrary rollback versions, multi-host orchestration, user-project deployment, or traffic shifting.
- No change to Code Agent's pre-existing Docker runtime. Its existing socket exposure is a separate security-hardening project; this change must neither widen it nor route Release Agent operations through it.
- No migration of database schemas outside the existing backward-compatible deployment practice; non-reversible migrations remain a manual release decision.

## Decisions

### 1. Split build authority from production deployment authority

The current release workflow will be split logically into:

1. A tag-triggered build workflow that checks out the tag, validates `GITHUB_REF_NAME == deploy/gap.version`, builds API and Web, captures each multi-platform `build-push-action` output digest, and uploads a small versioned release-manifest artifact.
2. A production deployment workflow defined on the protected default branch and activated only after the build workflow succeeds. It runs under the existing `production` GitHub Environment and on the production self-hosted runner. It downloads the source-run artifact by run id and does not check out the tag.

The manifest schema is fixed and includes: `schema_version`, `release_id`, `git_tag`, `version`, `commit_sha`, `target_id`, `api_image`, `web_image`, `created_at`, and `health_check_version`. Image references are canonical `repository@sha256:...` values, while `version` remains available for the GAP version display. The deployment job validates this schema and invokes only the installed host command with the manifest path.

`workflow_run` (or the equivalent GitHub default-branch-only trigger) is chosen because the production deployment definition is evaluated from the default branch rather than the tagged source. The alternative—keeping a `deploy` job in the tag-triggered workflow—would still permit a tag to change host-executed YAML even after moving `deploy.sh` out of the repository, so it is rejected.

### 2. Install a fixed, local Deploy Runner on the production host

The Runner is a systemd-managed host service with assets controlled outside an application release:

```text
/opt/gap-runner/
  bin/gap-deploy-runner       # fixed deploy/status/health/rollback implementation
  compose/gap-prod.compose.yml
  state/release-state.json
  state/success-history.json
  tls/                        # runner identity and GAP trust material
/opt/gap/.env                 # application secrets and ACR pull-only credentials
```

The protected GitHub deployment workflow uses only `gap-deploy-runner deploy --manifest <verified-file>`. The command validates the manifest schema, target id, canonical allowed API/Web repositories, SHA-256 digest form, and tag/version consistency before it performs a Compose operation. The fixed Compose template receives digest image references, never `latest`; it has no dependency on source files checked out by GitHub Actions.

The Runner exposes a separate, narrow mTLS HTTP control surface for `GET /v1/status`, `GET /v1/health`, and `POST /v1/rollback`. `deploy` is deliberately local-only, so GAP cannot initiate deployment. File ownership and systemd permissions limit Docker/Compose authority to the Runner service account. The Runner reads ACR pull credentials only from `/opt/gap/.env`; GitHub secrets for the deployment job contain no registry password.

The current API Docker socket mount continues only for the existing Code Agent runtime and is not an approved Runner transport or Release Agent interface. The Release Agent implementation contains no Docker client, shell executor, MCP tool, or generic URL/command bridge.

### 3. Model a release as a serialized, recoverable state machine

The Runner uses a durable exclusive target lock and records transitions such as `received`, `validating`, `deploying`, `verifying`, `succeeded`, `failed`, `rolling_back`, `rolled_back`, and `reconciliation_required`. A release is successful only when all Compose services are healthy/running and `GET http://127.0.0.1:${API_PORT}/health` returns `{"status":"ok"}` within the existing bounded window.

On success, the manifest becomes `last_known_healthy` and is appended to a five-entry success history. On validation, deploy, or health failure, the Runner records the failure and automatically re-applies the last-known-healthy manifest using the same fixed Compose asset. If no healthy baseline exists, it records `reconciliation_required` rather than guessing an image. New deployments are serialized; rollback requests have priority over queued deployments, while an already executing critical Compose command is allowed to reach a recorded safe transition before the higher-priority rollback begins.

The state files store fixed fields only: release/target identifiers, manifest digest fields, timestamps, phase, health observations, failure summary, previous healthy release, and callback delivery status. They never store secrets or command text. They remain the local operational source of truth when GAP cannot be reached.

### 4. Use bidirectional mTLS for the fixed Runner/GAP protocol

Runner and the GAP release-management API use the private `https://gap-runner.internal:9443` endpoint, protected by mutual TLS and host-gateway network restriction. Only the GAP API container resolves this name to the host's Docker gateway; the Runner binds its configured Docker-bridge address and host firewall policy permits this port only from that bridge. GAP uses the Runner endpoints to read status/health and request the constrained rollback. Runner calls a GAP result endpoint after each terminal transition and retries persisted unsent results with backoff.

The shared protocol uses idempotent release ids and a fixed JSON result envelope. GAP validates both peer identity and envelope shape, deduplicates repeated terminal notifications, and stores a release audit record. Authentication/validation failures are rejected without state change. This avoids a reusable shared webhook secret and makes a delayed callback safe to retry.

### 5. Implement Release Agent as a deterministic management surface

The API adds release manifest/audit persistence, a Runner client, reconciliation logic, and an internal Release Agent registration. The Web UI has a release status/history surface; all authenticated users with current management access can read redacted status, while rollback controls are rendered and accepted only for administrators.

An administrator rollback request displays the Runner-reported `last_known_healthy` release, requires the exact displayed target plus a confirmation phrase, creates a pending audit event, and submits a `POST /v1/rollback` request containing no user-specified image reference. Runner performs only its own stored baseline rollback. GAP later reconciles the terminal callback with the request record. In-progress or mismatched records show `reconciliation_required`; GAP never resumes a deployment blindly.

The optional LLM-facing explanation is a read-only rendering over persisted release records. It is not part of a ReAct tool loop and cannot choose a deployment command, image, target, or rollback version.

### 6. Keep deployment configuration intentionally small and auditable

New deployment configuration is limited to a target id, Runner base URL, CA bundle, GAP client certificate/key reference, and timeouts. ACR credentials remain host-only. The UI/API redact image registry credentials, private-key paths/content, and all unrelated application secrets. Installation documentation describes required protected GitHub rules, Runner service installation, host file ownership, certificate rotation, bootstrap behavior before a first healthy release, and the emergency direct-operator rollback path.

### 7. Use one host-managed Caddy service for domains and mTLS ingress

Caddy is the already installed host-level service and exclusively binds ports 80 and 443. `gapclaw.online` routes browser traffic to loopback-only GAP Web/API upstreams using a reviewed production Caddyfile. Later Compose stacks use their own explicit domain/host-loopback upstream pair; they do not bind 80/443 and cannot forge Caddy's verified-client identity. GAP API and Web bind only host loopback, never directly to the public interface.

`runner.gapclaw.online` exposes only `POST /internal/release-runner/callback`, requires a client certificate signed by the Runner private CA, removes any client-supplied identity header and sets the verified identity itself before proxying to GAP API. It is not a GAP-to-Runner control endpoint; all other public Runner-host requests receive no application route. A later Compose stack receives its own explicit Caddy site; this change does not add a catch-all route or a placeholder hostname for an unknown application.

The GAP mTLS client uses the fixed private `gap-runner.internal:9443` endpoint and validates the Runner certificate against its configured CA. The Runner callback client uses the fixed public `runner.gapclaw.online` callback URL. Caddy certificate material, the Runner CA and identities stay host-managed and are never included in application images, API responses or audit records. Caddy validates the reviewed main configuration before reload and does not overwrite it.

## Risks / Trade-offs

- [No cryptographic image signing in V1] → protected tags/default branch, digest pinning, protected Environment and host-local credentials provide a practical baseline; signing can be layered on later without changing the manifest interface.
- [A bad first release has no healthy rollback baseline] → Runner records an explicit non-recoverable state and operations use the documented emergency path; bootstrap first establishes a healthy baseline.
- [Runner or GAP restarts during a release] → durable state, exclusive lock recovery and explicit reconciliation prevent blind resumption.
- [mTLS certificate lifecycle adds operating work] → use one narrow private CA and documented rotation; no certificate/private key reaches browser clients or audit records.
- [Existing API socket exposure persists for Code Agent] → Release Agent has no code path to it and no Deploy Runner expansion; socket removal/isolation is tracked as a later independent hardening change.
- [Single-host Compose creates a brief restart window] → accepted V1 trade-off; blue/green and traffic switching remain future work.
- [One edge proxy becomes a shared availability dependency] → Caddy is independently restarted and each site is explicit; no Compose stack can claim 80/443 or intercept another stack by a default route.

## Migration Plan

1. Add and test the manifest generation/validation and default-branch deployment workflow without enabling production deployment.
2. Provision the host Caddy site, `gapclaw.online` DNS/TLS, Runner mTLS callback hostname, Runner host directory, service account, fixed Compose template and local ACR pull-only credentials; validate Caddy then verify public GAP routing and authenticated Runner callback manually.
3. Deploy the GAP API/Web release-management capability, bootstrap a known healthy manifest, and verify callback/audit reconciliation.
4. Enable the protected production Environment workflow for a controlled release. Exercise health success, failed health automatic rollback, GAP-unavailable callback retry, authorization denial, and administrator-confirmed rollback.
5. Retire tag-checkout deployment and repository-owned production script execution only after the new loop passes the controlled release checks. Rollback of the rollout is to disable the protected deployment workflow and invoke the documented Runner emergency operation; the Runner state remains available for diagnosis.
