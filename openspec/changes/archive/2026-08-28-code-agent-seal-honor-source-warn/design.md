## Context

See `proposal.md` for motivation. Publish and runtime already honor frozen `secret_policy.source` / `source_unscannable` via helpers in `scanner.py`. `CodeArtifactSealer.seal()` still requires `source_scan.complete` and `findings_count == 0`, so a warned-in snapshot cannot be sealed.

Constraints:
- Patch scan remains `block`.
- Snapshot is already sealed at encapsulate time; grilled decision: do not re-scan it.
- Existing tests assert missing ids / empty hashes → `artifact_evidence_missing`; default block must keep failing.
- Empty diff already maps to `no_change_justified` after a successful seal (`artifacts.py`).

## Goals / Non-Goals

**Goals:**
- One policy interpretation for source evidence at seal, shared with publish/runtime helpers.
- Keep default block and all non-source evidence checks (snapshot sealed, hash/commit match, image digest, policy hash).
- Regression tests that would have caught this drift.

**Non-Goals:**
- UI copy, persisting `sealing.reason` onto `failure_reason`, repository hygiene, Agent tool-loop, snapshot re-scan, changing patch policy.

## Decisions

### 1. Reuse existing source-policy helpers in the sealer

Replace the hard `complete && findings_count == 0` conjuncts with:
- `_source_scan_complete_or_warned(report, source_unscannable_action=...)`
- `findings_count > 0` allowed only when `_source_secret_policy_action(effective_policy) == "warn"`

Keep the rest of the existing conjunction (snapshot row, scope, sealed, hash/commit match, image digest, policy hash).

Rationale: publish/runtime already encode which incomplete reasons are warn-eligible (`scanner_binary_unsupported`, `scanner_unsupported_format`). A third copy will drift again.

Alternatives considered:
- Re-run `validate_source_scan_report` (includes disk re-scan) — rejected; snapshot is immutable and re-scan is slow/flaky on binaries.
- Allow only `scanner_binary_unsupported` — rejected; would ignore `source=warn` for secret findings, contradicting Q5/Q7.

### 2. Do not change `SealingResult.reason` or run `failure_reason` persistence

Failed default-block source evidence can remain `artifact_evidence_missing` / `infrastructure_error` as today.

Rationale: Q8 froze UI and reason persistence. Shot-1 success path does not need a better failure banner.

### 3. Tests at the sealer seam only

Extend `test_code_agent_artifacts.py`:
- Frozen policy `source=warn` + `source_unscannable=warn` with incomplete source report (unscannable reason) and `findings_count > 0` → seal succeeds; empty workspace diff → `no_change_justified`.
- Default/block policy with the same incomplete report or findings → still `artifact_evidence_missing`, no artifact.
- Existing missing-field parametrize and patch-secret tests remain.

## Risks / Trade-offs

- [Risk] Warn on source secrets means a sealed empty/non-secret patch can still sit on a repo that contains secrets → Mitigation: policy is explicit and already used at publish; patch stage still blocks new secrets; documented as warn not clean.
- [Risk] Helper import from `scanner.py` into `artifacts.py` creates a tighter coupling → Acceptable; they already share scan persistence types.
- [Trade-off] Incomplete source scans that are warn-eligible never get a “proven no secret” claim on skipped files → Spec already says they MUST NOT be treated as proven clean.

## Migration Plan

1. Land sealer + tests.
2. Restart local API so `--reload` picks up `artifacts.py`.
3. Operator re-runs `dbt_test` with the same trivial task; expect `no_change_justified` (or equivalent success), not infrastructure failure.
4. Rollback: revert sealer condition; no schema migration.

## Open Questions

None — grilled decisions cover scope.
