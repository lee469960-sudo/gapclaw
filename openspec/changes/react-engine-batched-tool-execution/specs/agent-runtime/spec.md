## ADDED Requirements

### Requirement: ReAct runtime supports explicit batched tool actions

The runtime SHALL allow one LLM iteration to request a batch of tool actions only when the batch declares an execution mode of `parallel`, `sequence`, or `transaction`. A batch without an explicit mode, a child action without a valid tool identity, or a child action that fails the same authorization checks as a standalone call MUST be rejected before execution. Existing single tool calls MUST continue to execute with their current behavior.

#### Scenario: Independent batch executes after one model turn

- **WHEN** a model response contains a valid `parallel` batch of independent authorized read-only tool actions
- **THEN** the runtime executes those child actions without requiring a separate LLM turn between each child action
- **AND** the next LLM input receives one compact batch result containing every child status

#### Scenario: Invalid batch does not partially execute

- **WHEN** a batch omits its execution mode or includes a child action that is not authorized for the current Agent/run
- **THEN** the runtime rejects the batch before executing any child action
- **AND** the model receives a compact failure result explaining the rejected child and reason

#### Scenario: Single tool behavior remains compatible

- **WHEN** a model response contains one legacy single tool action and no batch envelope
- **THEN** the runtime executes it using the existing single-call path and result shape

### Requirement: Batched execution respects dependency modes

The runtime SHALL interpret `parallel`, `sequence`, and `transaction` as different dependency contracts. `parallel` batches MUST execute only child actions declared independent. `sequence` batches MUST execute child actions in order and stop or mark downstream children skipped when an earlier required child fails. `transaction` batches MUST validate the whole write set before committing any write.

#### Scenario: Parallel batch reports independent child outcomes

- **WHEN** a `parallel` batch contains three independent read-only actions and one fails
- **THEN** the runtime reports one failed child and the successful child results
- **AND** it does not retry unrelated successful children through additional LLM turns

#### Scenario: Sequence batch stops after dependency failure

- **WHEN** a `sequence` batch child fails and later children depend on its output
- **THEN** the runtime does not execute the dependent later children
- **AND** the batch result marks them as skipped because of the earlier failure

#### Scenario: Transaction batch commits only after validation

- **WHEN** a `transaction` batch contains multiple file patch children
- **THEN** the runtime validates every patch before modifying files
- **AND** if any validation fails, none of the patches are committed

### Requirement: File read batching is complete-aware and token bounded

The runtime SHALL provide batch-safe file reading semantics that reduce page-by-page repeated inference while preserving completeness signals. Each file read child result MUST state whether the returned content is complete. Size-based shaping MAY return full content, structured ranges, summaries, or indexes, but it MUST indicate omitted ranges or truncation and provide enough location metadata for a later targeted read.

#### Scenario: Small files return complete content in one batch

- **WHEN** a batch reads multiple small files within the configured result budget
- **THEN** each child result includes the full file content and `complete: true`

#### Scenario: Large file read is explicitly incomplete

- **WHEN** a requested file exceeds the configured batch read budget
- **THEN** the child result includes a structured partial result with `complete: false`
- **AND** it identifies omitted ranges or follow-up read handles rather than implying the file was fully read

#### Scenario: Adjacent page reads can be coalesced

- **WHEN** the model requests adjacent ranges from the same file in a batch
- **THEN** the runtime MAY coalesce them into one physical file read
- **AND** the observable child results still preserve the requested ranges and completeness metadata

### Requirement: File write batching is transactional patch-only by default

The runtime SHALL support batch file writes through patch-style children that are dry-run validated as a group before commit. The default batch write path MUST NOT allow blind full-file overwrite. If any patch conflicts, targets a disallowed path, or fails validation, the runtime MUST leave every target file unchanged and return per-child diagnostics.

#### Scenario: All patches apply and commit together

- **WHEN** every child patch in a `transaction` batch passes authorization and dry-run validation
- **THEN** the runtime commits all patches
- **AND** the result reports the committed files and child statuses

#### Scenario: One patch conflict prevents all writes

- **WHEN** one child patch in a `transaction` batch does not apply cleanly
- **THEN** no child patch is committed
- **AND** the result identifies the conflicting child and target location

#### Scenario: Full file overwrite is rejected by default

- **WHEN** a batch write child requests an unrestricted full-file overwrite instead of a patch
- **THEN** the runtime rejects that child before commit
- **AND** no sibling write in the transaction is committed

### Requirement: Batch results are compact but inspectable

The runtime SHALL return a compact batch summary to the model and preserve inspectable details for users and debugging. The summary MUST include batch id, mode, total child count, success/failure/skipped counts, per-child identifiers, per-child status and concise retry hints for failures. Detailed stdout/content/diff data MAY be materialized or collapsed, but it MUST remain retrievable through recorded execution details when permissions allow.

#### Scenario: Model receives compact failure summary

- **WHEN** a batch has many child actions and several fail
- **THEN** the model receives a compact summary with failed child ids, reasons and suggested targeted follow-up actions
- **AND** the result does not inline excessive repeated output when a materialized detail reference is available

#### Scenario: User can inspect child details

- **WHEN** a batch tool call appears in the execution UI or audit stream
- **THEN** the user can expand it to see each child action, arguments subject to existing redaction, status, timing and detail reference

### Requirement: Batching does not expand security or routing authority

The runtime MUST apply the same security, sandbox, MCP routing, file path, secret redaction and permission checks to every batch child as it applies to an equivalent standalone tool call. A batch MUST NOT mix child actions across incompatible security domains, such as different sandboxes, unauthorized MCPs, or write scopes that cannot be validated under one transaction boundary.

#### Scenario: Unauthorized child blocks or fails safely

- **WHEN** a batch includes a child action targeting a file path, MCP, sandbox or tool the Agent cannot use
- **THEN** that child is rejected by the same authorization rule as a standalone call
- **AND** no transaction sibling is committed when the batch mode is `transaction`

#### Scenario: Cross-domain transaction is rejected

- **WHEN** a `transaction` batch attempts to combine writes across security domains that cannot share an atomic validation and commit boundary
- **THEN** the runtime rejects the batch before executing writes

### Requirement: Batch execution avoids unbounded automatic retries

The runtime SHALL NOT silently split, retry or loop batches without bounded policy. Automatic fallback MAY provide a single targeted retry or a smaller suggested batch only when the failure type is safe and observable; otherwise the model MUST receive the compact failure and decide the next action.

#### Scenario: Validation failure returns without retry storm

- **WHEN** a transaction batch fails dry-run validation
- **THEN** the runtime returns the validation failure result
- **AND** it does not repeatedly retry the same patch set without a new model action

#### Scenario: Safe fallback is bounded and visible

- **WHEN** the runtime performs an allowed automatic fallback for a transient child failure
- **THEN** the batch result records the fallback attempt and final status
- **AND** no child exceeds the configured retry limit
