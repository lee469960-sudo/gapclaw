## Context

See `proposal.md` for motivation. The current release workflow generates a canonical Hook envelope once, signs that exact byte stream, and delivers it to `https://gapclaw.online/internal/release-hook` with curl. GAP validates the HMAC, timestamp window, delivery id, target, tag/version, digest manifest, and then deduplicates the delivery before dispatching to the private Runner.

The current workflow uses curl retry flags, but the observed failure mode is an HTTP 403 that succeeds on a rerun. Depending on curl version and options, this can still fail the step without retrying the HTTP response in the way operators expect.

## Goals / Non-Goals

**Goals:**

- Retry the same signed Hook request for a small bounded window when delivery sees likely transient HTTP or network failures.
- Make retry behavior visible in CI logs without printing secrets or raw signed payloads.
- Keep duplicate protection and signature validation authoritative on GAP.

**Non-Goals:**

- Do not change GAP Hook authentication rules or treat 403 as success.
- Do not regenerate the signed envelope between retry attempts.
- Do not add production host, Docker, SSH, or Runner authority to GitHub CI.
- Do not alter Runner deployment, health checking, rollback, callback, or Release Agent behavior.

## Decisions

### Retry in the GitHub workflow, not inside GAP

The failure occurs before GAP accepts the delivery. Retrying in CI is the narrowest fix: it avoids changing the server's security behavior and keeps the Hook's existing deduplication as the final authority if multiple attempts arrive.

Alternative considered: accepting or softening 403 in GAP. Rejected because 403 is also the correct response for invalid signatures, invalid targets, and expired timestamps.

### Reuse one canonical envelope for every attempt

The workflow signs `.release/release-hook.json` once and reuses that file and signature for all attempts. This prevents retries from creating multiple logical delivery ids and relies on the existing timestamp window to bound replay.

Alternative considered: regenerate `issued_at` and `delivery_id` each attempt. Rejected because it would bypass deduplication and could create multiple release intake records.

### Retry only bounded, operator-observed transient classes

Retry attempts cover network failures and HTTP statuses that can represent startup/routing/edge races: 403, 408, 409, 425, 429, and 5xx. Attempts use exponential-ish sleeps within the existing release Hook timestamp window. Final non-2xx result fails the workflow and prints the response body for diagnosis.

## Risks / Trade-offs

- [Invalid secret repeatedly returns 403] → Retry wastes a short amount of CI time but still fails closed; GAP never accepts an invalid signature.
- [Timestamp expires during retry] → Keep the retry budget comfortably below `release_hook_max_age_seconds` default and do not regenerate the envelope.
- [Duplicate deploy] → Reuse the same delivery id and rely on GAP's existing durable deduplication before Runner dispatch.
