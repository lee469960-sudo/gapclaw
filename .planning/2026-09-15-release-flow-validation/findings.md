# Findings: GitHub → Release Agent production validation

## Initial state

- Current branch is `main`, aligned with `origin/main` at tagged commit `v1.0.21` (`eac37d1`).
- Remote lookup confirmed that `v1.0.22` does not exist; the release version has now been advanced from `v1.0.21` to `v1.0.22`.
- The worktree contains substantial verified application/runtime/UI fixes plus OpenSpec/planning changes; the release commit must exclude local-only `.claude/settings.local.json` and planning activation state.
- `.github/workflows/release.yml` is tag-triggered and requires the tag to exactly equal `deploy/gap.version` before building API/Web images and delivering the signed release hook.
- Production release task 5.4 remains unchecked and is the intended end-to-end evidence target.
- GitHub CLI is unavailable locally, so workflow monitoring will use GitHub's public Actions API.
- Production SSH currently requires password authentication; public-key-only access failed.
