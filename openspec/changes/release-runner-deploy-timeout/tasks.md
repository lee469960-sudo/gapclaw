## 1. Release Management read-path performance

- [x] 1.1 Decouple Release Management UI loading so status/history render without waiting for rollback-target discovery; verify the rollback card has its own loading/unavailable state.
- [x] 1.2 Add a bounded short timeout for rollback-target Runner status discovery while preserving deploy/rollback operation timeouts; verify timeout failures return unavailable without blocking status/history.
- [x] 1.3 Apply database limits and bulk manifest loading to release status/history; verify history limit is pushed to the query and manifests are not fetched one row at a time.

## 2. Verification

- [x] 2.1 Add/update backend tests for short Runner timeout selection, history limiting, bulk manifest data, and admin rollback-target behavior.
- [x] 2.2 Run focused release-management backend tests, frontend build, strict OpenSpec validation, and diff checks.
