## Context

See `proposal.md` for motivation. The current `agent-runtime` already defines ReAct protocol parsing, tool execution, MCP reuse/routing, coach prompts, result materialization and UI-visible execution steps. This change adds a batch execution layer on top of those existing single-call boundaries; it must not bypass authorization, MCP routing, sandbox constraints or existing single tool behavior.

## Goals / Non-Goals

**Goals:**

- Reduce repeated LLM turns for mechanically similar tool work, especially file reads and patch application.
- Preserve debuggability by making batch parent and child actions observable.
- Keep writes safe through patch-only transactional dry-run/commit semantics.
- Keep model context efficient through compact batch summaries and materialized details.
- Maintain existing security checks per child action.

**Non-Goals:**

- Do not create a general shell-command batch executor.
- Do not allow blind full-file overwrite as the default batch write primitive.
- Do not make batching a fallback that loads all MCPs or expands tool access.
- Do not require every existing tool to support batching in the first implementation.
- Do not tune model prompts as the sole mechanism; the runtime must enforce safety.

## Decisions

### Decision 1: Add a batch envelope rather than infer all batching implicitly

The runtime will accept an explicit batch envelope with `mode`, `children`, and stable child ids. The model may still emit legacy single actions.

Rationale: implicit merging is useful later, but an explicit envelope gives tests and UI a stable contract and prevents surprising writes.

Alternatives considered:

- Pure prompt instruction: simpler but unenforceable and hard to test.
- Fully automatic runtime coalescing only: useful for physical read optimization but insufficient to express dependency/transaction intent.

### Decision 2: Use three execution modes

The runtime will support:

- `parallel`: independent child actions, primarily read-only or diagnostics.
- `sequence`: dependent child actions executed in order.
- `transaction`: write set validated first, committed only after full success.

Rationale: this keeps the model from pretending dependent work is parallel and lets the runtime reject unsafe write batches.

Alternatives considered:

- One generic `batch` mode: too ambiguous for safety and failure semantics.
- Separate bespoke tool names for every pattern: fragments the protocol and makes UI/audit harder.

### Decision 3: Treat batch write as patch transaction

The initial write primitive will be patch-oriented. A transaction first performs authorization and dry-run validation for all patches, then commits all patches only if every validation succeeds.

Rationale: patch transactions are recoverable and reviewable. Full-file writes are too easy to misuse, especially when batching many files from one model turn.

Alternatives considered:

- `write_many_files`: faster for generated files but dangerous for existing files and harder to diff.
- Partial success: simpler to implement but creates inconsistent repository state and forces more repair turns.

### Decision 4: Separate model summary from inspectable execution details

Batch execution will produce two layers:

- compact model-facing result with counts, child statuses, short reasons and retry hints;
- detailed execution records/materialized artifacts for UI expansion and debugging.

Rationale: the model needs enough to decide the next action, not every byte of repeated output. Users still need traceability when a batch hides many operations behind one parent event.

Alternatives considered:

- Inline all child outputs: transparent but token-expensive.
- Only return aggregate status: token-efficient but causes blind follow-up reasoning.

### Decision 5: Authorization remains per child

Each child action goes through the same permission, MCP routing, file path, sandbox, redaction and logging checks as an equivalent standalone action. The batch layer is orchestration, not authority.

Rationale: batching must optimize execution, not become a privileged super-tool.

Alternatives considered:

- Authorize the parent batch once: simpler but risks smuggling unauthorized children.

### Decision 6: Start with file read and patch batches, then expand

The first implementation should cover:

- batch file reads/ranges with `complete` metadata;
- transactional batch patches;
- compact result shaping;
- UI event expansion;
- tests for security boundaries and failure behavior.

Rationale: these are the highest-value token-efficiency cases and easiest to validate. MCP batching can later reuse the same envelope but must respect lazy MCP routing.

## Risks / Trade-offs

- **Risk: batch summaries hide important context** → Mitigation: child details remain materialized/expandable, and summaries include `complete`/omitted-range metadata.
- **Risk: one failed patch blocks unrelated safe edits** → Mitigation: this is intentional for transaction safety; the failure result should suggest splitting independent patches when appropriate.
- **Risk: model overuses batch for dependent actions** → Mitigation: require explicit mode and skip dependent sequence children after prerequisite failure.
- **Risk: batching makes UI logs harder to read** → Mitigation: parent event shows summary; child rows are collapsed by default but expandable.
- **Risk: automatic retry recreates repetition under another name** → Mitigation: retries are bounded, visible and disabled for validation failures.

## Migration Plan

1. Add runtime batch schema and parser while preserving single action parsing.
2. Add result/event models that can represent batch parent and child records.
3. Implement read-only batch execution with compact results and complete metadata.
4. Implement transactional patch dry-run/commit semantics.
5. Update UI rendering to show collapsed batch parents and expandable child details.
6. Add prompt guidance so models prefer batch read/patch when appropriate.
7. Keep the feature compatible with existing agents; if batch parsing is disabled or invalid, legacy single-call behavior remains unchanged.

Rollback strategy: disable batch parsing/configuration and continue using legacy single tool execution. Because batch writes commit through the same file patch layer, no separate data migration is required.
