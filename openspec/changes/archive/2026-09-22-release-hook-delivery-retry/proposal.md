## Why

Tag-triggered CI/CD sometimes receives HTTP 403 from the fixed GAP release Hook immediately after image packaging, while manually rerunning the workflow succeeds. Operators need the workflow to tolerate short-lived edge, routing, or startup races without weakening Hook authentication or causing duplicate deployments.

## What Changes

- Add bounded retry behavior around the CI delivery of the already-signed release Hook envelope.
- Retry only transient delivery failures, including the observed early HTTP 403, 408/409/425/429, 5xx responses, and network/timeout errors.
- Keep the same canonical JSON body, HMAC signature, delivery id, and release manifest across all attempts.
- Preserve server-side fail-closed validation: invalid signatures, expired timestamps, invalid manifests, and duplicate delivery ids remain rejected by GAP and must not trigger a second Runner deploy.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `trusted-gap-deployment`: GitHub CI release Hook delivery gains bounded retry semantics while preserving fixed signed manifest and deployment authority boundaries.

## Impact

- Affected CI: `.github/workflows/release.yml` release Hook delivery step.
- Affected tests: workflow contract tests for Hook retry behavior.
- No database migration, no new secrets, no new Runner/API endpoint, and no change to Agent/MCP/channel runtime behavior.
