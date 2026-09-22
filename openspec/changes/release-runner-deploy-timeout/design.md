## Context

The Release Management page currently wraps all initial work in one `loading` state. For administrators, it loads `/api/release-management/rollback-target` after status/history. That endpoint calls `ReleaseRunnerClient.status()` with the same timeout used for deploy operations, which defaults to 60 seconds.

The page only needs the Runner-derived rollback target for the optional rollback card. The archived current release and history are already stored in GAP and can render independently.

## Decisions

### Separate archived status from live Runner probing

The UI will use separate loading state for:

- archived release status/history;
- administrator rollback-target discovery.

The archived data remains the primary page load. Rollback-target failures or timeouts show the existing unavailable message without blocking the rest of the page.

### Use a short timeout only for rollback-target discovery

Add a Runner client override for read-only status probes. The rollback-target endpoint will use a small timeout bounded below the deployment timeout. Deploy and rollback commands continue using the configured release runner timeout because those operations intentionally wait for Runner acceptance.

### Limit and bulk-load release history

Push the history limit into the database query and bulk-load matching release manifests for the selected audit rows. This avoids full-table reads and N+1 manifest lookups on every refresh.

## Compatibility and Safety

- Existing authenticated users can still read release status/history.
- Existing admin rollback confirmation still re-checks the Runner target before rollback submission.
- Existing Runner mTLS URL allow-list remains unchanged.
- No Agent, MCP, channel, or runtime deployment permissions are expanded.
