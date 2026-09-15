# Task Plan: GitHub → Release Agent production validation

## Goal

Publish the current verified GAP changes under the next patch tag and validate the complete GitHub Actions → signed release hook → GAP Release Agent → Deploy Runner → production health/audit flow for `gapclaw.online`.

## Current Phase

Phase 2 is in progress: run release-specific verification, prepare the `v1.0.22` commit and inspect its staged contents.

## Phases

| Phase | Status |
|---|---|
| 1. Inspect release contents and CI/production prerequisites | complete |
| 2. Run pre-release tests and prepare versioned commit | in_progress |
| 3. Create and push the next patch tag | pending |
| 4. Monitor GitHub build, image push and signed hook delivery | pending |
| 5. Verify Release Agent, Runner, production health and audit evidence | pending |

## Release Safety Rules

- Do not commit local-only settings, secrets or credentials.
- Tag must exactly match `deploy/gap.version` in the tagged commit.
- Do not bypass the GitHub signed-hook path by manually deploying.
- Treat the production domain, Runner status/health and release audit as separate required evidence.
- Preserve unrelated user changes; inspect all staged contents before commit.

## Errors Encountered

| Error | Attempt | Resolution |
|---|---:|---|
| Broad `find .. -name AGENTS.md` reached macOS-protected sibling data and reported `Operation not permitted` | 1 | No repository `AGENTS.md` was present; avoid broad parent traversal and keep searches inside the workspace. |
| `gh auth status` failed because GitHub CLI is not installed | 1 | Use the public GitHub Actions REST API and native `git` commands instead. |
| Sandboxed `git ls-remote` could not open SSH port 22 | 1 | Re-run the read-only remote check with approved network escalation. |
| Production SSH with `BatchMode=yes` returned `Permission denied` | 1 | The host does not have a usable local public key; use the previously authorized password through a scoped non-interactive SSH helper. |
| Two mTLS integration tests failed with `PermissionError: Operation not permitted` while binding `127.0.0.1:0` | 1 | This is the managed sandbox's local-socket restriction; rerun only those integration tests with approved escalation. |
| `git add` could not create `.git/index.lock` under the workspace-write sandbox | 1 | Re-run the explicitly authorized Git mutation with escalated filesystem permission. |
| Browser fetch rejected `https://gapclaw.online/health` as an unsafe URL before making a request | 1 | Use an approved read-only `curl` request for the production baseline. |
| Escalated HTTPS baseline request failed during TLS handshake with `SSL_ERROR_SYSCALL` | 1 | Retry once over explicit IPv4; use GitHub Hook delivery plus host-internal health as authoritative cross-check if the local network path remains unavailable. |
| Explicit IPv4 HTTPS baseline produced the same TLS handshake failure | 2 | Stop repeating the local path; proceed with GitHub delivery and production host evidence as planned. |
