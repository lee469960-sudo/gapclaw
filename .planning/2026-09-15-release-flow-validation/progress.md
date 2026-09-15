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
- 2026-09-15: User reported ICP/provider access has passed. Started a fresh release attempt from latest code, advanced `deploy/gap.version` to `v1.0.23`, and will validate the full GitHub-to-Release-Agent path with a new tag instead of rerunning the old failed `v1.0.22` job.
- 2026-09-15: `v1.0.23` pre-release checks passed: targeted API tests `37 passed`, web `npm run build` passed, `openspec validate release-agent-lightweight-deploy --strict` passed, `openspec validate react-engine-batched-tool-execution --strict` passed, and `git diff --check` passed.
- 2026-09-15: Public `https://gapclaw.online/health` now returns `{"status":"ok","version":"v1.0.21"}`, confirming the previous pre-HTTP TLS reset is cleared from this environment.
- 2026-09-15: Created commit `bc8ddc3` (`fix: harden agent tool execution`) and annotated tag `v1.0.23`; pushed both `main` and `v1.0.23` to GitHub successfully.
- 2026-09-15: GitHub release run `34953977621` started for `v1.0.23` at SHA `bc8ddc3417b1def4c4499c9675881f722909ca94`; job is currently in API multi-arch image build/push.
- 2026-09-15: `v1.0.23` images and immutable manifest succeeded, but signed Hook delivery failed with HTTP 503. Production API logs show the request reached `/internal/release-hook`; direct local/public probes return `{"detail":"release_hook_not_configured"}`. Root cause is missing production `RELEASE_HOOK_SECRET` in the API container environment.
- 2026-09-16: Fixed production Hook secret mapping by adding `RELEASE_HOOK_SECRET: ${GAP_RELEASE_HOOK_SECRET:?...}` to `/opt/gap-runner/compose/gap-production.compose.yml`, recreated the existing `gap-production-api-1` container with the current v1.0.21 immutable image values, and verified `RELEASE_HOOK_SECRET` is present, `/health` returns 200, and unsigned Hook POST now returns 403 `release_hook_signature_invalid`.
- 2026-09-16: Updated repo production compose to pass `GAP_RELEASE_HOOK_SECRET` into the API as `RELEASE_HOOK_SECRET`, advanced `deploy/gap.version` to `v1.0.24`, and will publish a fresh tag so the committed fix is included in the release evidence.
- 2026-09-16: Created commit `6ac814f` (`fix: pass release hook secret to production api`) and annotated tag `v1.0.24`; pushed both `main` and `v1.0.24` to GitHub successfully.
- 2026-09-16: `v1.0.24` GitHub run `34994891180` built and pushed API/Web images and generated/uploaded the immutable manifest successfully. Signed Hook delivery reached GAP but failed with HTTP 403.
- 2026-09-16: Production API was restored after testing a too-short Hook value: production containers are healthy, `/health` returns 200 with `v1.0.21`, and unsigned Hook POST returns 403 `release_hook_signature_invalid`, proving the Hook receiver is configured and failing closed.
- 2026-09-16: Attempted to update GitHub Actions secret `GAP_RELEASE_HOOK_SECRET` via REST API, but the current PAT returned 403 `Resource not accessible by personal access token`. Full release validation is blocked until the GitHub and production Hook secrets can be aligned.
