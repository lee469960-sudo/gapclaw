## ADDED Requirements

### Requirement: Release Agent synchronizes the deployed site version

Release Agent MUST expose a controlled capability that updates the public site version from the verified release manifest. The capability MUST use the manifest's validated version/tag, target and commit identity, MUST be idempotent for an already-applied version, and MUST NOT accept arbitrary URLs, commands, credentials, or caller-supplied deployment targets.

#### Scenario: Healthy deployment updates the site version

- **WHEN** a trusted Runner reports a deployment as healthy with a verified manifest
- **THEN** the Release Agent flow updates the site setting to the manifest version
- **AND** records the version-sync result with release identity and timestamp

#### Scenario: Duplicate version update is harmless

- **WHEN** the same healthy manifest is processed more than once
- **THEN** the site version remains the same
- **AND** the operation reports an idempotent success without creating conflicting settings

#### Scenario: Invalid or incomplete manifest is rejected

- **WHEN** the caller provides a manifest that has not passed release verification or omits required identity fields
- **THEN** the capability rejects the update
- **AND** no site setting is changed
- **AND** the rejection is auditable without storing credentials or the full signed payload

### Requirement: Release lifecycle notifications are delivered through the bound Release Agent channel

After a release lifecycle transition, GAP MUST send at most one notification for each transition to the dedicated channel bound to Release Agent. Notifications MUST cover healthy success, failure, and rollback outcomes and MUST contain only the sanitized release summary: version/tag, commit SHA, target, API/Web digests, health result, elapsed time, and failure or rollback summary when applicable.

#### Scenario: Successful deployment notification

- **WHEN** deployment and health checks complete successfully
- **THEN** the bound Release Agent channel receives one success notification
- **AND** the notification is sent after the site version synchronization result is known

#### Scenario: Failed or rolled-back deployment notification

- **WHEN** deployment fails or an automatic rollback completes
- **THEN** the bound Release Agent channel receives one failure or rollback notification
- **AND** the notification identifies the failure summary or rollback result

#### Scenario: Notification delivery failure is auditable

- **WHEN** the channel provider rejects, times out, or cannot deliver a release notification
- **THEN** the release lifecycle remains authoritative and unchanged
- **AND** the delivery failure is recorded for retry or operator inspection
