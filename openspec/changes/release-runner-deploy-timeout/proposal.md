## Why

The Release Management page blocks its whole opening and refresh flow while administrator-only rollback readiness waits for the Deploy Runner status endpoint. If the private Runner DNS, TLS handshake, or service is slow/unreachable, the UI can appear frozen until the release runner timeout expires.

Release history rendering also fetches all matching audit rows before slicing to the UI limit and resolves manifests one row at a time. This is unnecessary work for every page refresh.

## What Changes

- Decouple Release Management page loading so archived release status/history render before the rollback target probe finishes.
- Add a short, read-only Runner status timeout for rollback target discovery; deploy and rollback submission keep the existing configured timeout.
- Make release history/status reads apply the requested limit in the database and load manifests in bulk.
- Preserve Release Agent authority boundaries: no new deploy, shell, Docker, SSH, credential, or caller-selected Runner target capability.

## Capabilities

### Modified Capabilities

- `release-management`: page and read-only API behavior for status, history, and rollback-target discovery.

## Impact

- Affected API: release management status/history/rollback-target endpoints and release ledger reads.
- Affected Web UI: Release Management page loading states.
- Affected tests: Release Agent/rollback API tests and frontend build.
- Rollback risk: low; changes are read-path optimizations except the bounded timeout for rollback-target discovery. Rollback submission still validates the current Runner target immediately before sending rollback.
