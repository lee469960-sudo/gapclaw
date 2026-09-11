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

1. A tag-triggered GitHub-hosted build workflow that checks out the tag, validates `GITHUB_REF_NAME == deploy/gap.version`, builds API and Web, captures each multi-platform `build-push-action` output digest, and creates a small versioned release manifest.
2. The same CI job canonicalizes that manifest, assigns a unique delivery id and posts it to GAP's fixed HTTPS Hook with an HMAC-SHA256 signature and bounded timestamp. It does not invoke a server-side GitHub runner, transmit ACR credentials, or execute repository deployment scripts.

The manifest schema is fixed and includes: `schema_version`, `release_id`, `git_tag`, `version`, `commit_sha`, `target_id`, `api_image`, `web_image`, `created_at`, and `health_check_version`. Image references are canonical `repository@sha256:...` values, while `version` remains available for the GAP version display. The deployment job validates this schema and invokes only the installed host command with the manifest path.

The server does not fetch GitHub artifacts or register a GitHub Runner. GAP validates the fixed signed envelope before it can reach the private Runner; the Runner independently repeats manifest/repository/digest validation. This preserves tag-content isolation without requiring GitHub egress from production.

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

The Runner exposes a narrow mTLS HTTP control surface for `POST /v1/deploy`, `GET /v1/status`, `GET /v1/health`, and `POST /v1/rollback`. The deploy route accepts only the fixed complete manifest from GAP's client identity; it has no URL, command, tag or caller-selected target input. File ownership and systemd permissions limit Docker/Compose authority to the Runner service account. The Runner reads ACR pull credentials only from `/opt/gap/.env`; GitHub receives no registry password.

The current API Docker socket mount continues only for the existing Code Agent runtime and is not an approved Runner transport or Release Agent interface. The Release Agent implementation contains no Docker client, shell executor, MCP tool, or generic URL/command bridge.

### 3. Model a release as a serialized, recoverable state machine

The Runner uses a durable exclusive target lock and records transitions such as `received`, `validating`, `deploying`, `verifying`, `succeeded`, `failed`, `rolling_back`, `rolled_back`, and `reconciliation_required`. A release is successful only when all Compose services are healthy/running and `GET http://127.0.0.1:${API_PORT}/health` returns `{"status":"ok"}` within the existing bounded window.

On success, the manifest becomes `last_known_healthy` and is appended to a five-entry success history. On validation, deploy, or health failure, the Runner records the failure and automatically re-applies the last-known-healthy manifest using the same fixed Compose asset. If no healthy baseline exists, it records `reconciliation_required` rather than guessing an image. New deployments are serialized; rollback requests have priority over queued deployments, while an already executing critical Compose command is allowed to reach a recorded safe transition before the higher-priority rollback begins.

The state files store fixed fields only: release/target identifiers, manifest digest fields, timestamps, phase, health observations, failure summary, previous healthy release, and callback delivery status. They never store secrets or command text. They remain the local operational source of truth when GAP cannot be reached.

### 4. Use bidirectional mTLS for the fixed Runner/GAP protocol

GitHub CI calls `POST /internal/release-hook` through Caddy. GAP verifies the exact canonical manifest body using its host-provisioned HMAC secret, a bounded timestamp and an idempotent delivery id before persisting an intake audit. Only then does the non-LLM release intake service use the GAP mTLS identity at `https://gap-runner.internal:9443` to call fixed deploy. The private endpoint is reachable only from the GAP API container via Docker gateway; Runner callback retry remains unchanged.

The Hook and Runner protocol use fixed envelopes only. GAP records accepted delivery ids before deploy so retries cannot produce a second deployment; signature, timestamp, source event, target or manifest validation failure has no state transition. The HMAC secret is not exposed through API/UI/audit/logs; it is a GitHub CI secret and host-provisioned GAP secret, not an ACR credential.

### 5. Implement Release Agent as a deterministic management surface

The API adds release manifest/audit persistence, a Runner client, reconciliation logic, and an internal Release Agent registration. The Web UI has a release status/history surface; all authenticated users with current management access can read redacted status, while rollback controls are rendered and accepted only for administrators.

An administrator rollback request displays the Runner-reported `last_known_healthy` release, requires the exact displayed target plus a confirmation phrase, creates a pending audit event, and submits a `POST /v1/rollback` request containing no user-specified image reference. Runner performs only its own stored baseline rollback. GAP later reconciles the terminal callback with the request record. In-progress or mismatched records show `reconciliation_required`; GAP never resumes a deployment blindly.

The optional LLM-facing explanation is a read-only rendering over persisted release records. It is not part of a ReAct tool loop and cannot choose a deployment command, image, target, or rollback version.

### 6. Keep deployment configuration intentionally small and auditable

New deployment configuration is limited to a target id, Runner base URL, CA bundle, GAP client certificate/key reference, Hook HMAC reference and bounded Hook time window. ACR credentials remain host-only. The UI/API redact image registry credentials, private-key paths/content, Hook secret and all unrelated application secrets. Documentation describes GitHub CI Hook configuration rather than GitHub Runner installation.

### 7. Use one host-managed Caddy service for domains and mTLS ingress

Caddy is the already installed host-level service and exclusively binds ports 80 and 443. `gapclaw.online` routes browser traffic to loopback-only GAP Web/API upstreams and the exact `POST /internal/release-hook` path to the Hook receiver. Later Compose stacks use their own explicit domain/host-loopback upstream pair; they do not bind 80/443 and cannot forge Caddy's verified-client identity. GAP API and Web bind only host loopback, never directly to the public interface.

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

1. Add and test manifest generation/validation plus signed GitHub CI Hook delivery without enabling production deployment.
2. Provision the host Caddy site, `gapclaw.online` DNS/TLS, Runner mTLS callback hostname, Runner host directory, service account, fixed Compose template and local ACR pull-only credentials; validate Caddy then verify public GAP routing and authenticated Runner callback manually.
3. Deploy the GAP API/Web release-management capability, bootstrap a known healthy manifest, and verify callback/audit reconciliation.
4. After the user explicitly authorizes the production host, configure GitHub CI's Hook secret and run one controlled production Hook release with the existing Caddy backup and automatic rollback safeguards. Exercise signature denial/replay denial, health success, failed health automatic rollback, GAP-unavailable callback retry and administrator-confirmed rollback.
5. Retire GitHub self-hosted runner and repository-owned production script execution after the Hook loop passes controlled checks. Rollback is to disable Hook delivery and invoke the documented Runner emergency operation; the Runner state remains available for diagnosis.
