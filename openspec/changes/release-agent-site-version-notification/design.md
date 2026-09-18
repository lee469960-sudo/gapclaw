## Context

The existing release flow persists verified manifests and Runner lifecycle/audit records. Release Agent reads those records but does not currently trigger a site-settings synchronization or operator notification. HTTP MCP definitions and Feishu channels already have generic configuration and binding paths that should be reused rather than adding a second credential or webhook system.

## Goals / Non-Goals

**Goals:**

- Add one fixed, capability-scoped HTTP MCP operation for applying the verified release version to site settings.
- Trigger synchronization and notification from the authoritative release lifecycle transition, not from an LLM-generated command.
- Reuse Feishu channel persistence, webhook verification, delivery, retry, and Agent binding behavior.
- Keep all operations idempotent, auditable, secret-free in logs, and safe when notification delivery is unavailable.

**Non-Goals:**

- Grant Release Agent Docker, host shell, SSH, ACR, or arbitrary HTTP access.
- Allow a user or model to choose a URL, target, version, image, or signed manifest.
- Replace the existing GitHub hook, Deploy Runner, health checks, or release state machine.
- Broadcast release messages through unrelated Agent channels.

## Decisions

### 1. Use the verified manifest as the single version source

The lifecycle handler passes the already-validated manifest identity (`version/tag`, commit, target, digests) to a fixed internal site-settings operation. This is preferred over reading a mutable working-tree file or accepting a model argument because only a healthy, authorized release may change the public version.

### 2. Register a fixed HTTP MCP capability, not a generic proxy

The HTTP MCP integration exposes a named release version-sync operation whose server-side handler resolves the site-settings resource. The handler rejects arbitrary destinations and caller-supplied credentials, and checks that the invocation originates from the Release Agent release flow. Existing HTTP MCP discovery remains lazy; registering the capability does not connect to an external server during ordinary chat.

### 3. Couple synchronization to lifecycle transitions with idempotency

On a healthy transition, apply the version once and persist a deduplication key based on release identity and transition. Replays return the prior result. Failure and rollback transitions do not overwrite the site version; they still create notification delivery jobs and audit records.

### 4. Reuse a dedicated Feishu channel binding

Administrators create a separate Feishu channel instance and bind it to Release Agent using the existing provider credentials, webhook path, signature checks, and delivery queue. The release notifier targets only that binding. Missing or failed delivery is a warning/audit outcome and cannot change the authoritative deployment result.

### 5. Sanitize notification payloads

The message renderer includes version/tag, commit SHA, target, API/Web digests, health result, duration, and failure/rollback summary. It excludes HMAC signatures, bot secrets, ACR credentials, full prompts, and raw signed manifests.

## Risks / Trade-offs

- [Site version can lag if synchronization fails] → Persist the failure, expose it in Release Agent audit, and allow an idempotent retry against the same verified manifest.
- [Feishu delivery may be unavailable] → Keep deployment state authoritative, record delivery failure, and retry without rerunning deployment.
- [Duplicate lifecycle events] → Deduplicate by release identity plus transition before applying settings or sending a notification.
- [Configuration mistakes could bind the wrong Agent] → Require explicit Release Agent selection and show the binding in channel administration; do not infer it from channel name.
- [HTTP MCP surface could be over-broad] → Use a fixed operation and server-side authorization rather than a user-configurable proxy.

## Migration Plan

1. Deploy the capability and persistence migration behind a disabled-by-default notification setting.
2. Configure and verify a dedicated Feishu bot bound to Release Agent.
3. Enable healthy-release version synchronization and notifications, then test one staging or controlled production release.
4. If problems occur, disable the notifier/capability; release deployment and existing audit remain usable, and no rollback of deployed images is implied.
