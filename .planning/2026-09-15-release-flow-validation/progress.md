# Progress: GitHub → Release Agent production validation

## 2026-09-15

- Started release-flow verification at `main` / `v1.0.21`.
- Confirmed latest local tag is `v1.0.21` and the release workflow uses exact tag/version matching.
- Initialized isolated Planning with Files state for the production release validation.
- No tag, commit or push has been created yet.
- Confirmed remote `v1.0.22` is unused and updated `deploy/gap.version` to `v1.0.22`.
- Release regression first pass: 115 passed; two mTLS tests were blocked before assertions by the sandbox's loopback bind restriction.
- Both relevant OpenSpec strict validations, version equality and `git diff --check` passed.
- Re-ran the two sandbox-blocked mTLS integration tests with loopback permission: 2 passed.
- Local production HTTPS baseline did not reach the application because the local TLS path failed with `SSL_ERROR_SYSCALL`; no deployment mutation occurred.
- Explicit IPv4 produced the same pre-HTTP TLS failure; local public-path probing is closed and will not be retried unchanged.
- Created commit `30ea7f0` and local annotated tag `v1.0.22`; the first push did not reach GitHub, so no release workflow has started yet.
- HTTPS PAT push was rejected by GitHub with 403; remote `main` and tags remain unchanged.
- Confirmed password SSH login to the production host.
- Recorded healthy pre-release Runner baseline: service active, `status=ok`, `health=ok`, current successful release `v1.0.21-eac37d1`.
- Local release commit `30ea7f0` and annotated tag `v1.0.22` are ready; GitHub publication is the remaining prerequisite before workflow monitoring.
- GitHub now contains `main` commit `30ea7f0` and tag `v1.0.22`.
- Release workflow run `34917597143` started for the exact tagged SHA and is currently `in_progress`.
- Run `34917597143` completed with failure only at signed Hook delivery; API/Web images and immutable manifest succeeded.
- Diagnosed production ingress: Caddy/service/config/local TLS/health/host firewall all pass; GitHub's connection is reset before an HTTP Hook response.
- GitHub API denied failed-job rerun with 403; no destructive tag replacement was attempted.
- Final production cross-check: all GAP production containers are healthy; Runner status/health remain `ok` at `v1.0.21-eac37d1`; no `v1.0.22` Hook delivery, manifest record, audit or Runner transition exists.
- Phase 4 monitoring is complete. Phase 5 remains pending until public domain/provider access is cleared and the same failed job is rerun with Actions-write authority (or manually rerun by the repository owner).
