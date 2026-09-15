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
