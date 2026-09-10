# Findings: react-cached-mcp-convergence

## Initialization

- Formal change artifacts: `openspec/changes/react-cached-mcp-convergence/`.
- Authoritative task list: `openspec/changes/react-cached-mcp-convergence/tasks.md`.
- `openspec validate --strict` requires every original scenario name to remain in a MODIFIED requirement block; changed semantics are expressed by updating the scenario body while retaining its name.

## Baseline observations

- The current `子任务证据门` rejects FINAL before reflection whenever a named PLAN subtask is pending, which can bypass existing verifier convergence.
- MCP and generic tool dedup already preserve normalized action signatures and materialized result references; the new flow should reuse those signals rather than repeat calls.
- Existing final reflection emits prose FAIL/fix lists, so structured card parsing and checkpoint-compatible per-gap tracking are required.

## Decisions already confirmed

- A conditional, evidence-backed conclusion is deliverable for read-only/analysis work.
- Only unmet original user requirements and valid evidence gaps can block finalization; PLAN is advisory.
- Valid gaps need requirement, missing evidence, unexecuted action, and criterion; each allows two distinct action signatures.
- Runtime classifies action risk; ambiguous Shell commands are high risk, and unresolved high-risk work requests confirmation.
