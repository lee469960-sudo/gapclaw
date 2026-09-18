## 1. Release version synchronization

- [x] 1.1 Add the fixed HTTP MCP version-sync capability and server-side authorization for Release Agent; verify arbitrary URL, target, version, command, and credential inputs are rejected by API tests.
- [x] 1.2 Persist idempotent version-sync attempts keyed by verified release identity and transition; verify a replay leaves site settings unchanged and produces one auditable result.
- [x] 1.3 Trigger version synchronization only after a trusted Runner healthy transition; verify failed, unverified, and rolled-back releases never overwrite the public site version.

## 2. Release Agent notifications

- [x] 2.1 Implement sanitized success, failure, and rollback notification payloads containing version/tag, commit SHA, target, digests, health, duration, and outcome summary; verify secrets and raw signed payloads are absent.
- [x] 2.2 Connect lifecycle transitions to the dedicated Release Agent channel with per-transition deduplication and delivery audit/retry; verify provider timeout does not change deployment state.
- [x] 2.3 Add API and UI support to create an independent Feishu bot channel bound explicitly to Release Agent while preserving existing bindings; verify channel routing and webhook validation tests.

## 3. Integration and rollout

- [x] 3.1 Add end-to-end release lifecycle coverage for healthy version sync plus success notification, and failure/rollback notifications; verify duplicate hook/replay events do not duplicate side effects.
- [x] 3.2 Run focused Release Agent, HTTP MCP, site settings, and channel tests plus the relevant API regression suite; verify all pass and no existing channel behavior regresses.
- [x] 3.3 Document configuration, audit fields, retry/disable behavior, and safe rollback; verify a controlled release can enable/disable the capability without granting host or Docker access.
