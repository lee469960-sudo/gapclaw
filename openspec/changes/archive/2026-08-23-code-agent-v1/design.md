## Context

See `proposal.md` for motivation and the change specs for behavior. The current platform already has one `run_agent → AgentContext → AgentRuntime.run` lifecycle, a single ReAct loop, an action dispatcher, event publishing, run-state persistence and cleanup paths. CodeAgent must add code-oriented execution without creating a second task runtime or changing the default Standard Agent path.

The first release is deliberately constrained to allowlisted internal, non-production repositories. It produces reviewable patches only; it does not gain Git write credentials, general network access, real secrets, or production connectivity.

## Goals / Non-Goals

**Goals:**

- Compile an explicit Agent Profile into the existing runtime's immutable execution context.
- Give Code Profile runs isolated, reproducible source workspaces and policy-enforced code operations.
- Make verification and canonical patch sealing authoritative system steps rather than model claims.
- Preserve the existing Standard Profile behavior and common task/event/audit contract.
- Permit staged rollout, observability, and prompt rollback at project or platform scope.

**Non-Goals:**

- A second planning loop, separate task state machine, or dedicated CodeAgent product runtime.
- Automatic commits, pushes, branches, pull requests, deployments, production access, or real-secret injection.
- Arbitrary repository URLs, general internet access, user-supplied runners/images, dynamic tool loading, or shared writable workspaces.
- Making a container equivalent to strong isolation for untrusted third-party repositories; V1 only supports the defined trusted-repository tier.

## Decisions

### 1. Profile compilation happens before the ReAct loop

`run_agent` will resolve `standard` or `code` when it constructs the immutable context. A Code Profile resolver produces a `CodeExecutionContext` containing the frozen task contract, effective policy version, workspace reference, tool catalog, verification plan and resource limits. `AgentRuntime._run_modular` continues to own the single LLM-driven ReAct loop and receives only the unified context and permitted tools.

This keeps the existing loop, step stream, cancellation, checkpoints and final message behavior shared. The core loop must not accumulate `if code_profile` branches; Code-only behavior is expressed through context-provided hooks and tools.

**Alternatives considered:**

- A separate coding runtime: rejected because it duplicates task identity, events, budgets and audit/cleanup semantics.
- A hidden automatic Code Profile: rejected because it would change existing Agent behavior and make high-risk capabilities implicit.

### 2. Policy and task contract are control-plane inputs

Each Code project has a versioned Manifest that identifies its allowed repository, environment tier, paths, tests, tools, image, budgets and approvers. Platform → organization → project → profile → task policy is merged only by restriction. At run creation, the resolver freezes the effective policy and task contract; a missing/invalid Manifest cannot create a writable run.

The task contract stores the repository, base commit, objective, allowed paths, validation plan and budgets. Agent instructions, repository documents, model output and external issue text are data, not permission grants.

**Alternatives considered:**

- Free-form task text as the only contract: rejected because it cannot enforce scope or verify completion.
- Per-run mutable policy: rejected because it breaks auditability and permits mid-run escalation.

### 3. Workspace manager owns source integrity

A workspace manager creates one isolated, writable checkout per run from the manifest-authorized repository and frozen commit. The manager records source identity, validates clean state before sealing, and owns lifecycle cleanup. Concurrent runs may share a read-only source cache but never a writable directory. Source preparation disables repository-controlled execution during checkout; untrusted hooks, recursive submodules and similar execution surfaces remain disabled unless a later explicitly approved policy supports them.

For V1 the manager dispatches to a dedicated, resource-bounded container runner for trusted internal repositories. The runner mounts only the Workspace and explicitly declared temporary resources, defaults to no egress, and reports image/policy/runtime facts to the control plane. The component boundary permits a future microVM runner without changing the Profile or Runtime contract.

**Alternatives considered:**

- Reusing the platform application's working directory: rejected because concurrent runs and host data cannot be isolated.
- Shared project checkout plus file locking: rejected because it cannot prove a patch's source state or prevent cross-run contamination.

### 4. Code tools are typed policy-enforced adapters, not unrestricted shell

The runtime exposes read, search, patch/edit and test as first-class tools with structured inputs, path validation, redaction classification and auditable results. A restricted shell is a narrow fallback and is checked against the effective command/environment policy. Git is read-only in V1 for status and diff inspection; the canonical output is not a commit.

Every tool action performs deterministic preflight against the frozen capability set, allowed paths, resource limits and runner state before it executes. A rejection returns a safe, actionable result to the model without disclosing protected data. Repository content and command output are treated as untrusted input and cannot alter policy or tool permissions.

**Alternatives considered:**

- A single unrestricted shell tool: rejected because paths, commands, outputs and audit classifications cannot be reliably governed.
- Prompt-only restrictions: rejected because model instructions are not an enforcement boundary.

### 5. Verification and sealing are separate authoritative lifecycle phases

The ReAct loop can use test tools for exploratory feedback, but a Code modification enters a deterministic post-loop pipeline:

```text
prepare → execute/reason → verify → seal → patch_ready
                             │         │
                             └─ failure └─ infrastructure_error
```

The verifier executes the frozen validation plan and policy checks, including protected paths, secret detection and test-integrity rules. It compares relevant results with any recorded baseline and categorizes failure rather than relying on a model completion statement. On success, the artifact sealer regenerates the diff from the frozen workspace, computes hashes and records the base commit, policy, image and verifier report. Only the sealed artifact tuple is downloadable as `patch_ready`.

If verification is unavailable, the run ends as an explicitly non-success result such as `verification_inconclusive`; it may expose a clearly labeled reference patch under the project ACL, but it is not accepted or counted as successful.

**Alternatives considered:**

- Treating model `FINAL` as delivery: rejected because model claims do not prove workspace state or test execution.
- Letting the model choose whether to run final verification: rejected because verification is the primary Code Profile safety boundary.

### 6. Code-specific phases extend the common event envelope

Code runs emit common Runtime events plus a versioned Profile payload for preparation, workspace state, verification, policy rejections, sealing and cleanup. Existing consumers keep using the common envelope and ignore unknown payload fields. The control plane maps detailed Code outcomes into stable task results, including `patch_ready`, `no_change_justified`, `needs_clarification`, `stale`, `verification_inconclusive`, `workspace_integrity_error`, `budget_exhausted`, `no_progress`, `policy_rejected` and `infrastructure_error`.

Cancellation, timeout and runner loss block further actions, enter the normal Runtime termination path, revoke temporary resources and clean the workspace. A partial workspace is never an artifact.

### 7. Rollout is feature-gated and measured

The Code Profile is off by default and enabled only for an allowlist of internal non-production projects. V1 defaults are: 30 minutes per run, 40 model iterations, 5 concurrent runs per project, 20 changed files, 500 diff lines, 24-hour workspace retention and 30-minute approval TTL. Project policy can only reduce these limits.

The initial observation window is three internal repositories and ten low-risk bug-fix/test tasks over two weeks. Required signals include Standard Agent compatibility, policy/cleanup success, reproducible artifacts, verifier outcomes, human acceptance, elapsed time and cost. Security incidents pause expansion immediately. Layered kill switches can disable new Code runs globally or by project, repository, model, image or tool without changing Standard Agent availability.

### 8. Safety boundaries fail closed across admission, execution, cleanup and sealing

Run admission validates the selected Code project itself, not only its published Manifest. V1 accepts only enabled `internal_non_production` projects whose repository is the reviewed repository frozen into the Manifest; any production or unsupported tier is rejected before a run or writable Workspace exists.

The frozen run deadline applies to every container command, including exploratory Code Tools, baseline capture and final Verifier execution. Container execution must expose a cancellable process boundary and enforce the remaining run deadline independently of the async ReAct loop. Deadline expiry stops the command, blocks later actions and enters the common timeout/cleanup path.

Resource cleanup begins with the first successful allocation. Workspace preparation, runner startup and later phases register compensating cleanup as resources appear, and the outer Code lifecycle owns a single `try/finally` path. A preparation or startup failure records `infrastructure_error`, removes or freezes every partial writable resource and never leaves a `pending` run that can later resume accidentally.

Secret detection covers every byte eligible for sealing. Text, large and binary changes are scanned with bounded streaming; an unsupported, truncated or failed scan is a non-success verification result. Size limits may reject content before sealing, but they may never silently skip secret detection. The sealer accepts only a report that proves complete scanning of the exact changed content.

**Alternatives considered:**

- Treating project tier as deployment convention: rejected because rollout scope is a platform safety boundary and must be enforced at admission.
- Relying only on `asyncio` timeout around the ReAct loop: rejected because synchronous or remote container execution can outlive that coroutine.
- Registering cleanup only after full preparation: rejected because failures between allocations leave writable partial state.
- Skipping large or binary files during secret scanning: rejected because an attacker can choose file size or encoding to bypass delivery controls.

## Risks / Trade-offs

- [Container isolation is insufficient for untrusted code] → V1 restricts use to trusted internal repositories, denies privileged/nested-container access, and keeps the runner boundary replaceable by microVM isolation.
- [Manifest maintenance becomes burdensome] → provide a small set of platform-owned stack templates; only reviewed project deltas enter a Manifest.
- [Verification can be flaky or baseline already red] → capture baseline evidence when configured, classify inconclusive/pre-existing failures separately, and never turn them into `patch_ready`.
- [Code context and logs can expose sensitive content] → allowlist readable paths, redact known secrets, treat source as project-scoped, and keep raw artifacts under short-lived ACL-controlled retention.
- [Code execution can consume shared capacity] → use a separate runner queue and project quotas; Standard Agent latency and success are rollout guardrails.
- [A model may loop without useful progress] → enforce frozen budgets and emit `no_progress` diagnostics rather than silently retrying indefinitely.
- [A container command ignores cancellation or exceeds the run deadline] → enforce a runner-level deadline, terminate the command/container and route the run through common timeout cleanup.
- [Preparation fails after creating a writable resource] → register compensation per allocation and fail the run as `infrastructure_error` only after cleanup is attempted and audited.
- [A deliverable cannot be completely secret-scanned] → fail verification closed; never treat skipped, truncated or unsupported content as clean.

## Migration Plan

1. Add Profile support as an optional, default-`standard` configuration and verify existing Agent requests and events are unchanged.
2. Introduce the control-plane Manifest/policy model and read-only Code Profile preparation behind a global feature flag.
3. Add the isolated runner, typed Code Tools, Workspace manager, deterministic verifier and artifact sealing for allowlisted projects only.
4. Run compatibility, security-negative, cleanup and evaluation suites before admitting the default pilot projects.
5. Re-run admission, real runner-timeout, partial-start cleanup and large/binary secret-negative tests; do not begin the pilot while any fail-closed gate is incomplete.
6. Observe the pilot for two weeks; expand only when the agreed quality, safety and cost thresholds are met.

Rollback disables the relevant Code Profile feature flag or kill switch, preventing new Code runs and safely terminating/reclaiming active Code resources. It does not migrate, alter or disable Standard Agent configuration or execution paths. Sealed audit metadata remains subject to its retention policy.
