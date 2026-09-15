# Findings: GitHub → Release Agent production validation

## Initial state

- Current branch is `main`, aligned with `origin/main` at tagged commit `v1.0.21` (`eac37d1`).
- Remote lookup confirmed that `v1.0.22` does not exist; the release version has now been advanced from `v1.0.21` to `v1.0.22`.
- The worktree contains substantial verified application/runtime/UI fixes plus OpenSpec/planning changes; the release commit must exclude local-only `.claude/settings.local.json` and planning activation state.
- `.github/workflows/release.yml` is tag-triggered and requires the tag to exactly equal `deploy/gap.version` before building API/Web images and delivering the signed release hook.
- Production release task 5.4 remains unchecked and is the intended end-to-end evidence target.
- GitHub CLI is unavailable locally, so workflow monitoring will use GitHub's public Actions API.
- Production SSH currently requires password authentication; public-key-only access failed.
- Production pre-release baseline is healthy: `gap-deploy-runner` is active and both Runner `status` and `health` report `status=ok`, `phase=succeeded`, release `v1.0.21-eac37d1`, target `production`.
- GitHub network/auth state: SSH ports 22 and 443 time out from this environment; the supplied PAT receives repository push 403; the OS credential helper has no usable GitHub credential. A current HTTPS write token is therefore required to publish `30ea7f0` and `v1.0.22`.
- Release run `34917597143` built and pushed both multi-architecture images and generated/uploaded the immutable manifest successfully. Only the final signed Hook step failed; GitHub curl received three TLS-level `Connection reset by peer` errors before any HTTP response.
- Production Caddy is active, listens on public 80/443, validates cleanly, and serves `gapclaw.online/health` internally with HTTP 200. UFW is inactive, nftables INPUT policy is accept, and the domain resolves to the expected host.
- Adapted Caddy TLS policies correctly isolate callback mTLS to SNI `runner.gapclaw.online`; the main `gapclaw.online` Hook route does not request client certificates. No safe Caddy configuration defect was found.
- Production API logs and release tables contain no `v1.0.22-30ea7f0` intake; Runner logs contain no new deployment. Production remains entirely on healthy `v1.0.21-eac37d1`, proving there was no partial deployment.
- This reproduces the existing task 5.4 public-domain gap documented on 2026-09-12: DNSPod/provider public ingress blocks or resets the domain before Caddy. Completing ICP/provider release is required before rerunning the failed GitHub job.

## 2026-09-15 v1.0.23 retry after ICP approval

- User reported ICP/provider access has passed and requested a fresh latest-code commit, new tag and complete release flow validation.
- The retry will use `v1.0.23` because `v1.0.22` already exists remotely and records the previous pre-ICP failed Hook attempt.
- Public `https://gapclaw.online/health` now returns HTTP success with `status=ok` and version `v1.0.21`; this is the expected pre-release production baseline before the new GitHub Hook deploys `v1.0.23`.
- Pre-release validation for the current latest-code batch passed: targeted API tests, web production build, both relevant OpenSpec strict validations, and whitespace diff check.
- `v1.0.23` GitHub run `34953977621` proved the public Hook route now reaches GAP API: the Hook step received HTTP 503 responses, API access logs recorded the POSTs, and local/public unsigned POST probes both returned `{"detail":"release_hook_not_configured"}`.
- The root cause is production API configuration, not Caddy or public ingress: `deploy/docker-compose.prod.yml` did not pass `RELEASE_HOOK_SECRET` into the API container, so `settings.release_hook_secret` remains empty and the Release Hook service rejects all deliveries before signature validation.
- Server-side correction: `/opt/gap/.env` already had `GAP_RELEASE_HOOK_SECRET`; the production compose needed to map it into the API process as `RELEASE_HOOK_SECRET`. After recreating `gap-production-api-1`, unsigned Hook probes return 403 `release_hook_signature_invalid`, proving the API now has a configured secret and fails closed at signature validation.
- `v1.0.24` Hook failure is no longer ingress or missing configuration: GitHub receives HTTP 403 from GAP. The previous user-supplied Hook value is shorter than the API's 32-character minimum, so it cannot be used as the production Hook secret. A new 32+ character secret must be written to both GitHub Actions `GAP_RELEASE_HOOK_SECRET` and production `/opt/gap/.env`.
- The current GitHub PAT can push code/tags and read Actions logs, but cannot read/update repository Actions secrets. GitHub returned 403 `Resource not accessible by personal access token` when attempting to get the repository Actions secrets public key.
- Production has now been updated to the user-supplied long Hook secret and remains healthy. The remaining gap is repository-side only: GitHub Actions `GAP_RELEASE_HOOK_SECRET` must be manually set to the same value or updated with a token authorized to manage Actions secrets; the current token also cannot rerun failed jobs.
- After the user selected rerun option 1, the current PAT still could not start `rerun-failed-jobs` for run `34994891180` and returned 403. The next executable action is external to the current token: click GitHub's "Re-run failed jobs" for that run or provide an Actions-write token.
- Manual rerun attempt 2 for run `34994891180` still returned HTTP 403 from the GAP Hook. Since production now has a configured 32+ character secret and unsigned probes return `release_hook_signature_invalid`, the remaining mismatch is repository-side: GitHub Actions secret `GAP_RELEASE_HOOK_SECRET` must be set to the same user-supplied value before another rerun.
- Attempting to use local browser automation to update GitHub Actions secrets was blocked by the CUA runtime before the browser opened. This did not mutate GitHub and does not change the diagnosis.
