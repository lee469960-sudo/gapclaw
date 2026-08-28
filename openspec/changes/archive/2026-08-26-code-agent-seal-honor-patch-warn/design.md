## Context

`code-agent-seal-honor-source-warn` 只放宽 source 证据。patch 仍硬 block。`merge_policy_layers` 甚至拒绝 `patch: warn`。

Constraints:
- Incomplete patch scans stay fail-closed; no `patch_unscannable`.
- Platform default remains `patch: block`.
- Published Manifests are immutable; live projects need a new draft + publish.

## Goals / Non-Goals

**Goals:**
- Accept `patch: warn` in policy merge.
- Verifier + Sealer + review honor frozen `patch=warn` for complete scans with secret findings.
- Keep default block and incomplete-scan fail-closed tests green.

**Non-Goals:**
- Changing platform default or Operator UI defaults.
- Treating truncated/binary patch scans as warn.
- Redacting secret values out of sealed patch bytes (warn means the artifact may contain the matching lines).

## Decisions

### 1. Continue hashing after a secret hit in `scan_changed_content`

Today a hit returns `complete=False` and an empty hash, so Sealer cannot accept a passing verifier report. Warn needs `complete=True`, `detected=True`, and a stable `content_hash`. Block still fails on `detected`.

### 2. Reuse a `_patch_secret_policy_action` helper next to the source helpers

Same parse/fail-closed-to-block pattern. Sealer skips `findings_count` / `content_scan.findings` only when the action is warn; hash mismatch and incomplete scans still fail.

### 3. Review uses the sealed policy.json

`load_verified_bundle` currently requires zero findings and a complete source scan. Without aligning review, a warned patch cannot be accepted, and `dbt_test`'s incomplete source scan would still block accept. Reuse `_source_scan_complete_or_warned` and the source/patch action helpers on the sealed policy bytes.

## Risks / Trade-offs

- [Risk] Sealed artifacts may contain password-like lines (e.g. dbt `profiles.yml`) → Accepted; policy is explicit warn, not clean.
- [Trade-off] Platform default stays block, so operators must set Manifest JSON and republish.

## Migration Plan

1. Land merge + verifier + sealer + review + tests.
2. Restart local API (WatchFiles may miss nested modules).
3. Publish a new Manifest with `secret_policy.patch=warn` for the target project.
4. Rollback: revert honor checks; merge can keep allowing the value unused.

## Open Questions

None — user asked to honor `patch=warn`; incomplete patch scans stay fail-closed.
