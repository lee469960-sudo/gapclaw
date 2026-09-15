## Why

ReAct Engine currently spends too many LLM turns on mechanically similar tool work, such as reading adjacent file pages one at a time or applying small related edits across files. This increases token use, latency and the chance of repeated reasoning loops; the runtime needs a safe batching contract that reduces repeated inference without hiding failures or expanding tool authority.

## What Changes

- Add batched tool execution semantics to the ReAct runtime: a single LLM step can request a batch of tool actions when their dependency mode is explicit.
- Introduce three batch execution modes:
  - `parallel` for independent read-only or diagnostic calls.
  - `sequence` for dependent calls that must run in order.
  - `transaction` for write batches that require dry-run validation before commit.
- Add batch-oriented file read behavior with complete/incomplete indicators, size-aware content shaping and result compaction.
- Add transactional batch patch behavior: patches are dry-run as a group and committed only if every patch applies.
- Add structured batch failure reporting so the model receives a compact summary, per-item status and actionable retry hints.
- Extend execution logs/UI payloads so users can expand a batch tool call and inspect each child action.
- Preserve existing single-tool behavior and security boundaries; batching MUST NOT authorize tools, MCPs, files or sandboxes outside the current run's existing permissions.

## Capabilities

### New Capabilities

- None.

### Modified Capabilities

- `agent-runtime`: Adds safe batched tool execution semantics for ReAct tool actions, file reads, transactional file patches, compact results, failure handling and observability.

## Impact

- Affected backend runtime code: ReAct action parsing, tool dispatch, tool result recording, coach prompts, checkpoint/log structures and completion review context.
- Affected tools: file-read/read-range style tools, patch/apply-patch style tools and any runtime wrapper that can expose batch-safe child calls.
- Affected UI/API: execution event shape for expandable batch parent and child tool calls.
- No breaking change is intended for existing agents or single tool calls; batching is opt-in through the runtime protocol and guarded by existing authorization checks.
