## 1. Completion contract and persistent state

- [x] 1.1 Add backward-compatible runtime state for validated verifier gaps, per-gap action signatures, evidence summaries, and terminal reason; verify legacy checkpoint load and state round-trip tests pass.
- [x] 1.2 Replace the pending-PLAN FINAL hard gate with user-requirement and validated-gap finalization flow; verify a pending semantic-review subtask no longer blocks an evidence-backed FINAL.

## 2. Verifier gap validation and bounded convergence

- [x] 2.1 Define and parse the structured verifier gap-card response, preserving raw malformed verdicts as observable non-blocking notes; verify complete and malformed card cases with focused unit tests.
- [x] 2.2 Validate each gap against normalized action signatures, query cache/materialized paths, executed results, and declared criterion; verify duplicate, cached, and already-satisfied gaps do not re-execute or reject FINAL.
- [x] 2.3 Enforce two distinct evidence-gaining actions per valid gap and produce a conditional final after low-risk exhaustion; verify repeated reasoning cannot exhaust the main iteration budget.

## 3. Risk-aware terminal behavior

- [x] 3.1 Add conservative action-risk classification, including static Shell classification where ambiguous commands are high risk; verify representative read, redirect/write, delete, network, deployment, and ambiguous commands.
- [x] 3.2 Route unresolved high-risk gaps to an explicit confirmation/authorization response without auto-executing or declaring completion; verify user-visible terminal step and absence of the high-risk execution.

## 4. Regression coverage and validation

- [x] 4.1 Add an end-to-end ReAct regression for the `ReplacingMergeTree`/`FINAL` business-equivalence loop, including pending PLAN state; verify it returns a qualified final rather than repeating inference.
- [x] 4.2 Run focused ReAct, parser, checkpoint, MCP dedup, and action-risk tests; verify all pass and `git diff --check` is clean.
