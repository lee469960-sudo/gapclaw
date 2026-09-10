## Context

See `proposal.md` for motivation. The current runtime records an LLM-owned PLAN in checkpoint state and applies an early FINAL gate whenever a named subtask remains pending. The gate runs before final reflection and therefore bypasses the existing bounded convergence for repeated reflection failures. MCP and generic tool dedup already retain normalized call signatures and materialized result paths, but the verifier communicates only prose repair lists.

## Goals / Non-Goals

**Goals:**

- Make finalization depend on user requirements and evidence rather than speculative PLAN state.
- Give the runtime deterministic data to decide whether a verifier request has information gain.
- Bound repeated verification while preserving cautious handling of side effects.
- Reuse existing action normalization, query caches, artifacts, checkpoints, and user-visible steps.

**Non-Goals:**

- Rebuild MCP clients, alter MCP protocols, or remove result materialization.
- Treat a conditional answer as proof that an external mutation occurred.
- Depend on any one provider/model to recognize repeated reasoning.

## Decisions

### Separate delivery contract from execution plan

The raw user goal is the delivery contract. PLAN subtasks remain valuable context and progress display, but no pending PLAN entry can reject FINAL on its own. This replaces the current all-subtasks hard gate; keeping it would preserve the exact liveness failure. Removing PLAN entirely was rejected because it still supports resumability and model guidance.

### Use runtime-validated verifier gap cards

The verifier will be prompted to return a machine-parseable list of gap cards, each with `requirement`, `missing_evidence`, `action`, and `criterion`. Runtime acceptance requires all fields plus an action signature that has not already been executed and whose criterion is not already satisfied by observed output, saved artifacts, or cache/materialization metadata. Unparseable and prose-only FAILs are non-blocking, continuing the existing treatment of vague failure as acceptance. Prompt-only filtering was rejected because weak models are the source of the loop.

### Track evidence attempts per gap, not globally

Checkpoint state will persist a stable gap identity, seen action signatures, and completed evidence summaries. A gap can request two distinct action signatures. A duplicate signature is neither executed nor counted; a successful different action updates the evidence summary. This replaces generic repeated-reflection convergence for these gaps with a reasoned bound. A single global retry budget was rejected because unrelated gaps could consume one another's evidence allowance.

### Classify risk from executable actions

An action classifier will label known read/query operations low risk and known mutation/external-effect operations high risk. Shell classification uses a conservative static rule: recognizable read/query commands are low risk, while redirection, mutation, deletion, network egress, deployment commands, or ambiguous syntax are high risk. The model cannot self-declare an action safe. Classifying all shell use as high risk was rejected because it would block data-analysis workflows; trusting model labels was rejected for safety.

### Terminal outcomes remain explicit

For exhausted low-risk gaps, runtime asks the existing final distillation path (or deterministic fallback) to state evidence, conditions, and remaining uncertainty. For exhausted high-risk gaps, it produces a confirmation/authorization request with the proposed action and evidence. Both paths write an observable step so UI and resume state explain why execution stopped.

## Risks / Trade-offs

- [Gap parser rejects a legitimate verifier answer] → treat it as non-blocking and retain the raw verdict in an observable step; require focused regression coverage for malformed outputs.
- [Evidence criterion cannot be mechanically evaluated for every tool result] → use conservative evidence matching and let an unresolved low-risk gap become an explicit condition, never a fabricated PASS.
- [Shell static classification has blind spots] → ambiguous syntax defaults high risk; keep the classifier deliberately conservative and test representative command families.
- [Checkpoint compatibility] → new fields use empty defaults when loading old runs.

## Migration Plan

1. Add backward-compatible checkpoint fields and parser/classifier helpers.
2. Replace the early PLAN-subtask FINAL gate with validated user-requirement/evidence-gap flow.
3. Add regression tests for the reported ReplacingMergeTree/FINAL loop, cached MCP references, malformed gaps, bounded alternatives, and high-risk escalation.
4. Deploy with existing logging; monitor terminal reason and gap-card validation events. Roll back by disabling the new finalization branch while retaining saved checkpoints and caches.
