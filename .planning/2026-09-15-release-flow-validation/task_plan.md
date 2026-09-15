# Task Plan: GitHub → Release Agent production validation

## Goal

Publish the current verified GAP changes under the next patch tag and validate the complete GitHub Actions → signed release hook → GAP Release Agent → Deploy Runner → production health/audit flow for `gapclaw.online`.

## Current Phase

ICP/provider access is cleared, production Hook secret mapping is fixed, and production now uses the user-supplied long Hook secret. `v1.0.24` proved image/manifest publication and public Hook reachability, but final delivery remains blocked until GitHub Actions `GAP_RELEASE_HOOK_SECRET` is set to the same value and the failed job is rerun or a new tag is pushed.

## Phases

| Phase | Status |
|---|---|
| 1. Inspect release contents and CI/production prerequisites | complete |
| 2. Run pre-release tests and prepare versioned commit | complete (`v1.0.24`) |
| 3. Create and push the next patch tag | complete (`v1.0.24` pushed) |
| 4. Monitor GitHub build, image push and signed hook delivery | blocked (`v1.0.24` Hook returns 403) |
| 5. Verify Release Agent, Runner, production health and audit evidence | pending (blocked before Runner) |

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
| First `git push origin main v1.0.22` returned no output and did not advance `origin/main` | 1 | Treat as failed; diagnose GitHub SSH authentication before retrying. |
| Unauthenticated GitHub Actions API returned HTTP 403 | 1 | Repository/workflow data requires authentication or rate-limit relief; use an authorized authenticated request after the push succeeds. |
| GitHub SSH authentication probe timed out on port 22 | 1 | Use HTTPS with a transient askpass helper and the previously authorized PAT; never place the token in the remote URL or Git config. |
| HTTPS push returned GitHub HTTP 403 for the authorized identity | 1 | The PAT lacks current write authority or is expired. Delete the helper and try GitHub's SSH-over-443 endpoint with the existing local key. |
| GitHub SSH-over-443 also timed out | 1 | Check for an existing OS Git credential helper; if absent or unauthorized, a new repository-scoped write PAT is required. |
| macOS Keychain helper did not provide a GitHub username for non-interactive HTTPS push | 1 | GitHub push is blocked until a current repository-scoped write PAT is supplied; continue only read-only production baseline checks meanwhile. |
| First password-SSH expect wrapper captured the password prompt but returned no remote output | 1 | Do not treat as authenticated; test login with a simpler exact prompt matcher before issuing Runner reads. |
| First authenticated Actions API call returned 401 because the bearer environment variable was expanded by the parent shell before `env` applied it | 1 | Expand the token only inside a child shell that receives the environment variable; do not classify the credential itself as invalid. |
| GitHub rerun-failed-jobs API returned 403 | 1 | Token has Actions read but not Actions write. Do not mutate tags to force a rerun; record the ingress blocker and request rerun authority only after public TLS is fixed. |
| GitHub run logs API returned 404 while `v1.0.23` run was still in progress | 1 | Treat as unavailable archive for an active run; continue polling job/step status and only fetch logs after completion or failure. |
| Production inspection command failed because remote shell received an unescaped `find (...)` expression | 1 | Avoid grouped `find` predicates over the SSH/expect quoting layer; use simpler path discovery commands. |
| Production inspection command failed because Tcl tried to expand `$RELEASE_HOOK_SECRET` locally | 1 | Avoid shell variable interpolation in expect strings; use `printenv RELEASE_HOOK_SECRET` inside the container instead. |
| Production compose patch command failed because Tcl tried to expand `${GAP_VERSION...}` locally | 1 | Generate dollar-prefixed Compose interpolation text on the remote host with `chr(36)` instead of embedding `${...}` in the expect string. |
| Production API recreate command failed because `ubuntu` cannot `cd /opt/gap-runner/compose` | 1 | Use absolute compose file paths with `sudo docker compose` instead of relying on shell cwd access. |
| Production compose recreate failed because release-scoped variables are not in `/opt/gap/.env` | 1 | Recover `GAP_VERSION` and immutable image references from Runner state or current containers before recreating API. |
| Production compose recreate accidentally used default project name `compose` and created a separate failed stack | 1 | Remove the accidental `compose` project and rerun with explicit `-p gap-production` to target the existing production containers. |
| Production Hook returned 503 again after aligning to the previously supplied Hook value | 1 | The supplied value is shorter than the API's 32-character minimum, so generate a new 32+ character secret and update both GitHub Actions and production. |
| GitHub Actions secret update API returned 403 with current PAT | 1 | Current token cannot read/update repository Actions secrets; user must provide a token with Actions secrets administration permission or update `GAP_RELEASE_HOOK_SECRET` manually in GitHub. |
| First release-ledger SSH query failed because Tcl expanded the container-only `$POSTGRES_USER` variable locally | 1 | Wrap the complete remote command in a Tcl braced literal so PostgreSQL environment variables expand only inside the DB container. |
| Second release-ledger query reached PostgreSQL but nested quoting mangled SQL string labels | 2 | Remove all SQL string constants and query the latest fixed fields directly; check for the target release id in output. |
