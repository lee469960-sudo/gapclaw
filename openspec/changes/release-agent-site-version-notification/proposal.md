## Why

Release Agent can currently report deployment state, but a successful deployment does not automatically synchronize the public site version or notify operators through a dedicated message bot. This leaves the visible version and operational channel out of sync with the verified release result.

## What Changes

- Add a controlled HTTP MCP capability for synchronizing the site version from the verified release manifest.
- Permit only the built-in Release Agent flow to invoke the version synchronization capability; do not accept arbitrary URLs, versions, commands, or credentials.
- Update the site version only after deployment health checks succeed, with idempotent behavior and an auditable result.
- Add a dedicated Feishu channel instance that reuses the existing Feishu configuration and webhook validation flow, bound to Release Agent.
- Push one release notification after success, failure, or rollback, including the verified release and health details without secrets.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `release-management`: synchronize the public site version through a controlled Release Agent capability and emit lifecycle notifications.
- `channels`: support a dedicated Feishu bot binding for Release Agent release notifications.

## Impact

- Affected API: Release Agent/release lifecycle integration, HTTP MCP tool registration, site settings update path, and channel dispatch.
- Affected Web UI: HTTP MCP and message-channel configuration may expose the new controlled capability and Release Agent binding guidance.
- Affected persistence/audit: version-sync attempts and notification delivery outcomes must be recorded without storing credentials or full signed payloads.
- No change to Deploy Runner authority: Release Agent still cannot execute arbitrary host, Docker, SSH, or ACR operations.
