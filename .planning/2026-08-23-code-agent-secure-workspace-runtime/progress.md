# Progress Log: code-agent-secure-workspace-runtime（Implementation）

## Session: 2026-08-23

### Current Status

- **Tracking state:** archived (verify + spec sync complete)
- **Current execution phase:** Phase 9 complete; workflow closed
- **OpenSpec completion:** 41 / 41 tasks
- **Implementation status:** Phases 1–9 implemented; continuation verify fixed 2 regressions; main specs synced; change archived
- **Archive:** `openspec/changes/archive/2026-08-25-code-agent-secure-workspace-runtime/`
- **Task checkboxes changed during initialization:** none



## Session: 2026-08-25 (Cursor continuation)

### Reality check vs planning claims

- Planning/OpenSpec claimed Phases 1–9 and 41/41 tasks complete; implementation modules present.
- First re-verify under sandbox produced many false failures (`.git` PermissionError cleanup).
- Outside sandbox: **2 real failures** among CodeAgent tests; progress claims were ahead of current code behavior.

### Fixes applied before archive

1. `apps/api/app/services/code_agent/artifacts.py`: treat only `findings_count` (secret_pattern) as `secret_detected`; incomplete binary scans surface `scanner_binary_unsupported` via `failure_reason`.
2. `apps/api/tests/test_code_agent_lifecycle_persistence.py`: clear module cleanup/chat state and use unique run ids to avoid cross-test hook pollution on `run1`.

### Verification evidence

- `pytest -q tests/test_code_agent*.py` (apps/api, host/no-sandbox) → **560 passed** (EXIT 0).
- `openspec validate --specs` → 12 passed after sync.
- Delta specs synced into main for: control-plane, project-policy, repository-source (new), sandbox-runtime (new), verification, workspace.
- Archived change to `openspec/changes/archive/2026-08-25-code-agent-secure-workspace-runtime/`.
- `openspec list` → no active changes.


### Initialization Actions

- 依据已完成的 grilling 决策选择首个 change：`code-agent-secure-workspace-runtime`。
- 明确与后续 `code-agent-skill-context`、`code-agent-usage-validation` 的依赖和范围边界。
- 初始化独立 Planning with Files 目录并切换 active plan。
- 创建 OpenSpec change scaffold，并依据现有主 specs 完成 `proposal.md`。
- Proposal 声明两个新 capability（repository source、sandbox runtime）及四个 modified capabilities。
- 创建 proposal 要求的全部六个 delta specs；新增 capability 均含 Purpose，existing capability 仅使用 ADDED/MODIFIED delta。
- 将来源限制、Secret 引用、ref 冻结、SSRF/重定向、全工具容器化、Compose bind path、fail-closed 扫描与双层清理语义转为可测试场景。
- 完成设计文档，确定 Source→Snapshot→Workspace 三层、typed runner protocol、双根 bind path、持久化生命周期 janitor 与双阶段 scan report。
- 创建 9 个依赖有序 task groups；每个 checkbox 都包含独立测试、命令或可观察验证，后续 apply 仅以该 `tasks.md` 为正式任务来源。
- 完成 strict validation 与 artifact graph 核对；proposal/specs/design/tasks 均为 `done`，planning complete。
- 重新完整读取当前 change 的 proposal、design、六个 delta specs 和 tasks。
- 将 Planning with Files 从 artifact creation 记录切换为 implementation execution tracking。
- 将 9 个 OpenSpec task groups 映射为 9 个 execution phases，没有复制或重写正式需求。
- 建立逐 task 完成门禁：代码/交付物完成、验证通过、progress 记录完成后，才允许勾选 `tasks.md`。
- 确认 `.planning/.active_plan` 指向本 change 的执行跟踪目录。
- 初始化后重新严格校验 OpenSpec change，并确认 `tasks.md` 仍为 41 个未勾选任务。
- 用户明确执行 `opsx:apply code-agent-secure-workspace-runtime`；Phase 1 已进入 in progress，尚未修改业务代码。
- OpenSpec apply instructions loaded: state `ready`, schema `spec-driven`, progress 0/41, no extra project context or operation guidance.
- All CLI-listed context files re-read completely; implementation may begin at task 1.1.

### Validation

| Check | Result |
|---|---|
| Session catch-up | no unsynced context |
| Proposal dependency/context review | passed |
| Delta specs strict validation | passed after preserving existing Verifier scenario identity |
| Artifact graph after design | tasks ready |
| `openspec validate code-agent-secure-workspace-runtime --strict` | passed |
| Final artifact graph | planning complete; all artifacts done |
| Business code edits | none |
| OpenSpec tasks at execution initialization | 41 unchecked; 0 completed |
| Implementation/tests during initialization | not started / not run |
| Active Planning with Files pointer | `2026-08-23-code-agent-secure-workspace-runtime` |
| Initialization strict revalidation | `openspec validate code-agent-secure-workspace-runtime --strict` passed |
| Formal task checklist integrity | 41 unchecked; no checkbox modified |

### OpenSpec Task Completion Ledger

| Task | Code / deliverable | Verification evidence | Progress recorded | `tasks.md` checked | Status |
|---|---|---|---|---|---|
| 1.1 | RepositorySource/SourceSnapshot/ScanReport models plus reversible schema migration | `pytest -q tests/test_code_agent_secure_schema.py tests/test_startup_migrations.py` → 6 passed; targeted `git diff --check` passed | yes | yes | complete |
| 1.2 | Manifest/run frozen source, snapshot, image, schema and policy-hash contract | `pytest -q tests/test_code_agent_control_plane.py tests/test_code_agent_secure_schema.py tests/test_startup_migrations.py` → 31 passed; targeted `git diff --check` passed | yes | yes | complete |
| 1.3 | Secure source/import/retention/workspace-root/image config and fail-closed startup readiness | `pytest -q tests/test_code_agent_config.py tests/test_startup_migrations.py tests/test_code_agent_control_plane.py` → 32 passed; targeted `git diff --check` passed | yes | yes | complete |
| 1.4 | Legacy Manifest republish migration and admission block with Standard Agent compatibility | Phase 1 suite → 70 passed; full control-plane UI rerun → 30 passed; targeted `git diff --check` passed | yes | yes | complete |
| 2.1 | Organization/project/resource-scoped admin/owner/operator/reviewer service authorization and denial audit | focused authorization/control-plane/artifact/startup/Standard suite → 93 passed; all CodeAgent tests → 153 passed; full `tests/` suite → 427 passed; targeted `git diff --check` passed | yes | yes | complete |
| 2.2 | Encrypted read-only Deploy Token Secret Store with reference-only metadata and Importer-scoped zeroizing lease | focused Secret/schema/auth/startup suite → 30 passed; all CodeAgent tests → 164 passed; full `tests/` suite → 437 passed; targeted `git diff --check` passed | yes | yes | complete |
| 2.3 | Named six-layer policy intersection/union/minimum merge with frozen source/image facts and limiting-source provenance | focused policy/control-plane/startup suite → 75 passed; all CodeAgent tests → 182 passed; full `tests/` suite → 455 passed; targeted `git diff --check` passed | yes | yes | complete |
| 2.4 | Stable stage/reason taxonomy, actionable external mapping and allowlist-sanitized traceable security audit | focused failure/auth/Secret/result suite → 55 passed; all CodeAgent tests → 212 passed; full `tests/` suite → 485 passed; targeted `git diff --check` passed | yes | yes | complete |
| 3.1 | Pure remote-source canonicalizer, exact pre-connect origin allowlist and shared deploy-time readiness validation | focused source/config/failure suite → 73 passed; all CodeAgent tests → 249 passed; full `tests/` suite → 522 passed; Python compile and scoped tracked diff check passed | yes | yes | complete |
| 3.2 | Per-connection DNS/IP guard, pinned validated addresses, explicit internal CIDRs and controlled per-hop redirect transport | focused network/source/config/failure/authorization suite → 113 passed; all CodeAgent tests → 277 passed; full `tests/` suite → 550 passed; Python compile and scoped tracked diff check passed | yes | yes | complete |
| 3.3 | Canonical local-root admission and fd-relative no-follow copy into disjoint Importer staging | focused local-source/config suite → 28 passed; all CodeAgent tests → 293 passed; full `tests/` suite → 566 passed; Python compile and trailing-whitespace check passed | yes | yes | complete |
| 3.4 | Isolated Git clone/ref/archive importer with pinned remote transport, feature denials and hard resource/deadline cleanup | focused Git/network/failure/config suite → 86 passed; all CodeAgent tests → 314 passed; full `tests/` suite → 587 passed; Python compile and trailing-whitespace check passed | yes | yes | complete |
| 3.5 | Sanitized immutable content-addressed snapshot store with exact commit Git metadata and tamper verification | focused Git/snapshot suite → 27 passed; all CodeAgent tests → 321 passed; full `tests/` suite → 594 passed; Python compile and trailing-whitespace check passed | yes | yes | complete |
| 3.6 | Transactional Manifest reference counting, atomic zero-ref cleanup claim, staging/orphan janitor and persistent retry state | focused lifecycle/snapshot/schema/startup suite → 22 passed; all CodeAgent tests → 327 passed; full `tests/` suite → 601 passed; Python compile, scoped tracked diff and trailing-whitespace checks passed | yes | yes | complete |
| 4.1 | Structured Manifest source/image schema, owner-scoped approved option API, reference-only credential metadata and option-driven editor | focused API/source/local/Secret/config suite → 113 passed; web production build passed; all CodeAgent tests → 336 passed; full `tests/` suite → 610 passed; Python compile and whitespace checks passed | yes | yes | complete |
| 4.2 | Fail-closed publish pipeline (permission → source/secret/image policy → ref resolve/import → source scan → snapshot seal → transactional freeze) wired through router, with per-step fault-injection isolation tests | `pytest -q tests/test_code_agent_manifest_publish.py` → 9 passed; full `pytest -q tests` → 621 passed | yes | yes | complete |
| 4.3 | Fine-grained `secure_readiness_reason` (source/auth/ref/snapshot/image/mount) shared by `project_availability` and `create_code_run`, with distinct stable reasons surfaced in list/detail and enforced as a run-admission gate | `pytest -q tests/test_code_agent_readiness.py` → 14 passed; full `pytest -q tests` → 635 passed | yes | yes | complete |
| 4.4 | `create_code_run` writes a `run_create` `CodeControlAudit` fact freezing commit/snapshot/digest/policy-hash, and run admission never re-resolves the ref or re-imports from Git | `pytest -q tests/test_code_agent_run_baseline.py` → 4 passed; full `pytest -q tests` → 639 passed | yes | yes | complete |
| 5.1 | `WorkspaceManager.prepare` materializes the sealed snapshot into a per-run workspace (verifying snapshot hash, exact commit, run identity and sanitized Git metadata) with no clone/network/Secret-Store access | `pytest -q tests/test_code_agent_workspace_snapshot.py tests/test_code_agent_workspace.py tests/test_code_agent_runtime_context.py` → 27 passed; `pytest -q tests -k code_agent` → 372 passed; full `pytest -q tests` → 646 passed | yes | yes | complete |
| 5.2 | `WorkspaceIntegrityGuard` extended with snapshot identity, cross-run mount and uncontrolled-checkout facts; per-run directories remain isolated; each failure marks `workspace_integrity_error` and blocks tools/patch | `pytest -q tests/test_code_agent_workspace_integrity.py` → 6 passed; `pytest -q tests -k code_agent` → 378 passed; full `pytest -q tests` → 652 passed | yes | yes | complete |
| 5.3 | `workspace_mount.resolve_workspace_host_path` maps API-root → host-root with containment + run-sentinel identity; `prepare` writes the sentinel; the runner binds the validated host path and fails closed on `workspace_mount_invalid` | `pytest -q tests/test_code_agent_workspace_mount.py tests/test_code_agent_runner.py` → 17 passed; `pytest -q tests -k code_agent` → 388 passed; full `pytest -q tests` → 662 passed | yes | yes | complete |
| 5.4 | Compose declares `CODE_WORKSPACE_API_ROOT`/`CODE_WORKSPACE_HOST_ROOT` dual roots under the single data bind mount; `code_agent_workspace_mapping_readiness` read-only probe verifies same-storage; `ensure_dirs` creates the API root; deploy-config validation test asserts the two roots share one storage | `pytest -q tests/test_code_agent_config.py tests/test_code_agent_deploy_config.py` → 21 passed; full `pytest -q tests` → 670 passed | yes | yes | complete |
| 6.1 | `runner_protocol.py` defines frozen `RunnerSpec` (image digest, security options, network, mount, complete budgets), `RunnerBudgets`, and typed `RunnerToolRequest`/`RunnerToolResponse` with per-tool parameter schemas and bounded-output enforcement; all deserialize fail closed | `pytest -q tests/test_code_agent_runner_protocol.py` → 36 passed; full `pytest -q tests` → 706 passed | yes | yes | complete |
| 6.2 | Standalone stdlib-only `runner_helper.py` (read/search/edit/git-status/diff/log/test/shell) with `O_NOFOLLOW` fd containment, atomic temp-file+rename writes, fixed git commands, no-shell command execution and bounded output; `deploy/code-agent-runner.Dockerfile` bakes it into a minimal git-enabled image | `pytest -q tests/test_code_agent_runner_helper.py` → 28 passed; full `pytest -q tests` → 734 passed | yes | yes | complete |
| 6.3 | `CodeContainerRunner` builds a validated `RunnerSpec` from the frozen policy, launches by pinned digest as non-root (`65534:65534`) with read-only rootfs, cap-drop ALL, no-new-privileges, network none, single rw Workspace mount and every budget (pids/tmpfs/CPU/memory/time/output/disk) applied; `verify_inspect_matches` fails closed on any Docker inspect drift | `pytest -q tests/test_code_agent_runner.py` → 13 passed; full `pytest -q tests` → 744 passed | yes | yes | complete |
| 6.4 | `CodeToolExecutor` rewired so read/search/edit/git/test/shell all route through `CodeContainerRunner.run_tool` (typed `RunnerToolRequest` on stdin → `RunnerToolResponse` on stdout to the baked helper, no shell); deleted host `Path.read_text`/`rglob`/`write_text` and `subprocess` git branches; monkeypatch-forbidden-fs/subprocess tool suite proves the six tools still work | `pytest -q tests/test_code_agent_tools_routing.py` → 4 passed; `pytest -q tests/test_code_agent_tools.py tests/test_code_agent_security.py` → 15 passed; full `pytest -q tests` → 748 passed | yes | yes | complete |
| 6.5 | `CodeContainerRunner._preflight` enforces per-call active-lifecycle + run/container binding (rejects inactive runs, mismatched `container_id`, cross-run container reuse, stale containers); `run_tool` detects OOM exit 137 and helper spawn failure as `RunnerResourceLimitError`; `CodeToolExecutor` maps `RunnerUnavailableError`/`RunnerProtocolError`→`infrastructure_error`, `RunnerResourceLimitError`→`resource_limit_exceeded`, `RunnerCommandTimeout`→`timed_out`, each blocking later tool calls | `pytest -q tests/test_code_agent_preflight.py` → 13 passed; `pytest -q tests/test_code_agent_tools_routing.py tests/test_code_agent_tools.py tests/test_code_agent_runner.py tests/test_code_agent_security.py` → 45 passed; full `pytest -q tests` → 761 passed | yes | yes | complete |
| 6.6 | Shared `AgentRuntime` remains the only ReAct task/event orchestration; Code Profile injects only its frozen `CodeToolExecutor`; every Code tool audit binds run/container/policy facts; Standard Profile has no Code context/executor and allocates no snapshot/Workspace/runner | focused runtime/Standard/tools suite → 34 passed; all CodeAgent tests → 489 passed; full `pytest -q tests` → 765 passed; Python compile and targeted whitespace checks passed | yes | yes | complete |
| 7.1 | Frozen `ScanReport`/`ScanFinding`, strict hash-bound pass predicate and fail-closed `BuiltinSecretScanner`; publish persistence records scanner/version, input hash, coverage counts and redacted findings without conflating coverage with findings | focused scanner/publish/verifier/artifact suite → 39 passed; all CodeAgent tests → 496 passed; full `pytest -q tests` → 772 passed; Python compile and targeted whitespace checks passed | yes | yes | complete |
| 7.2 | Pre-allocation `validate_source_scan_report` binds the persisted source report to the sealed snapshot, rescans the immutable source and requires exact scanner/version/hash/coverage/no-findings before Code Workspace or runner creation | focused source-gate/runtime/publish/scanner suite → 34 passed; all CodeAgent tests → 499 passed; full `pytest -q tests` → 775 passed; Python compile and targeted whitespace checks passed | yes | yes | complete |
| 7.3 | After runner freeze, `CodeArtifactSealer` builds the canonical diff and persists one patch `ScanReport` covering changed/new content plus the canonical diff; findings, incomplete coverage and verifier input drift block sealing | focused scanner/verifier/artifact suite → 34 passed; all CodeAgent tests → 503 passed; full `pytest -q tests` → 779 passed; Python compile and targeted whitespace checks passed | yes | yes | complete |
| 7.4 | Artifact v2 seals source/patch ScanReport bytes and hashes together with Verifier hash, snapshot id/hash, resolved commit, image digest and frozen policy hash; review independently verifies every file/hash/link | focused result/artifact suite → 23 passed; all CodeAgent tests → 511 passed; full `pytest -q tests` → 787 passed; Python compile and targeted whitespace checks passed | yes | yes | complete |
| 8.1 | Persistent run/runner cleanup eligibility with post-start route activation, atomic terminal revocation, reverse-order resource cleanup and fail-closed post-terminal tool admission | focused lifecycle/runtime/security/runner/migration suite → 60 passed; all CodeAgent tests → 517 passed; full `pytest -q tests/` → 793 passed; Python compile and targeted whitespace checks passed | yes | yes | complete |
| 8.2 | Frozen platform/Project Workspace retention with 168-hour default, immediate-delete option, full-tree read-only retention and retained remount/download denial independent of artifact/audit records | focused retention/workspace/runner/control-plane/migration suite → 95 passed; all CodeAgent tests → 523 passed; full `pytest -q tests/` → 799 passed; Python compile and targeted whitespace checks passed | yes | yes | complete |
| 8.3 | Persistent lifecycle janitor with startup crash recovery, minute polling, bounded retry schedule, sanitized runner/Workspace cleanup alerts and run-bound Docker deletion | focused janitor/lifecycle/runtime/workspace/runner/migration/failure suite → 85 passed; all CodeAgent tests → 527 passed; full `pytest -q tests/` → 803 passed; Python compile and targeted whitespace checks passed | yes | yes | complete |
| 8.4 | Snapshot/Workspace/disk capacity metrics, computed and manual storage kill switches, admission low-water gate and pressure reclaim ordered as expired Workspace then unreferenced snapshot | focused storage/config/control-plane/snapshot/janitor/retention suite → 62 passed; all CodeAgent tests → 534 passed; full `pytest -q tests/` → 811 passed; Python compile and targeted whitespace checks passed | yes | yes | complete |
| 9.1 | Pre-route in-container bind identity probe plus real Docker daemon API→runner→API mutation and fail-closed wrong-root/cross-run/extra-mount/socket/digest release matrix | real Docker suite → 3 passed; focused unit suite → 38 passed; non-privileged CodeAgent suite → 534 passed, 3 skipped; full `pytest -q tests/` → 822 passed, 3 skipped; no leaked test container; Python compile/whitespace passed | yes | yes | complete |
| 9.2 | Consolidated Repository Importer security release matrix across source/network/Git/local/Secret/publish/failure suites | matrix suite → 146 passed; latest full suite → 822 passed, 3 Docker-permission skips | yes | yes | complete |
| 9.3 | Full publish-to-cleanup workflow gate combining real publish/snapshot, scan, offline Workspace, six tools, Verifier/patch scan, artifact and terminal cleanup with branch-drift/offline-source invariants | workflow suite → 97 passed; latest full suite → 822 passed, 3 Docker-permission skips | yes | yes | complete |
| 9.4 | Complete API, migration, control-plane/RBAC/policy, Workspace/runner/tools/Verifier/artifact/lifecycle, frontend and Standard Agent regression evidence | full API → 822 passed, 3 Docker skips; CodeAgent → 534 passed, 3 skips; migration/control-plane/Standard → 46 passed; web production build passed; real Docker → 3 passed | yes | yes | complete |
| 9.5 | Release-environment runner build with selectable Debian mirrors, exact RepoDigest verification, Compose trusted-digest readiness, real Docker inspect/bind/cleanup recovery, and strict OpenSpec validation | runner image built as `code-agent-runner@sha256:aed8…eef3f`; non-root/read-only/no-network image probe passed; real Docker → 4 passed; config/deploy → 22 passed; Compose config passed with exact digest; zero leaked runner containers; strict OpenSpec validation passed | yes | yes | complete |

### Task 9.4 Completion Evidence

- Full API suite covers database migrations, control plane/API, RBAC, effective policy, Workspace, runner protocol/helper/preflight, six tools, scans, Verifier, artifacts, lifecycle/capacity and shared ReAct runtime.
- Dedicated Standard/control-plane/migration rerun passed 46 tests and preserves the no-Code-resource allocation boundary for Standard Agents.
- Frontend `npm run build` completed 1,899 module transforms and emitted the production bundle; only existing Rollup annotation/chunk-size warnings remain.
- Non-privileged full runs explicitly skip the three real-Docker tests because sandbox daemon access is unavailable; the same three tests passed 3/3 under the approved Docker release environment.
- Verification evidence: API 822 passed + 3 skips; CodeAgent 534 passed + 3 skips; workflow 97 passed; Importer matrix 146 passed; migration/control-plane/Standard 46 passed; frontend build passed; real Docker 3 passed.
- Implementation and verification are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 9.5 Completion Evidence

- Parameterized the runner Dockerfile's Debian and security mirrors while retaining the official endpoints as portable defaults; all three probed release mirrors returned HTTP 200.
- Built `code-agent-runner:1` with a reachable mirror and resolved the local RepoDigest `code-agent-runner@sha256:aed8f7617a64c887c5e7cc1ea195e9f3c7e750812dca8b3b8916eb8b052eef3f`.
- Probed that exact digest as user `65534:65534` with read-only rootfs, network none, cap-drop ALL and no-new-privileges; Git 2.47.3 and the baked non-empty runner helper were available.
- Ran the real-Docker bind/inspect, wrong-root, cross-run/extra-mount/socket/digest-rejection and persisted startup cleanup recovery suite against the exact release digest: 4 passed; no labeled runner container remained.
- Injected the exact RepoDigest through `CODE_TRUSTED_IMAGE_DIGESTS` and ran `docker compose -f deploy/docker-compose.yml config --quiet`: passed, with only the existing obsolete top-level `version` warning.
- Re-ran deploy/security readiness tests: 22 passed. Re-ran `openspec validate code-agent-secure-workspace-runtime --strict`: passed.
- Code, tests, deployment gates and strict OpenSpec validation are complete; this evidence is recorded before updating the formal task checkbox.

### Task 9.3 Completion Evidence

- Established one release workflow command spanning publish→sealed snapshot/source report→offline Workspace→all six runner-routed tools→Verifier and patch report→artifact review→terminal cleanup.
- Publish tests use real local Git input and assert the frozen exact snapshot; Workspace tests forbid importer/network/Secret access; six-tool tests assert only the runner adapter executes; artifact tests bind both scan reports and verifier evidence before cleanup tests revoke execution.
- The same gate includes repeated-run and existing-run baseline tests under forced Git importer outage plus simulated branch drift; commit/snapshot/policy facts remain frozen.
- Local/import/security portions assert mutations occur in staging/Workspace rather than the approved source repository, preserving original repository bytes.
- Verification: the ten-suite workflow gate passed 97 tests; latest full regression remains 822 passed with only 3 expected non-privileged Docker skips (the real Docker gate separately passed 3/3).
- Implementation and verification are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 9.2 Completion Evidence

- Audited the existing scenario inventory before adding anything: HTTPS/SSH/internal HTTP and pinned DNS/redirect handling are covered by source/network/Git suites; private token and invalid authorization by Secret Store/Git suites; local escape and race cases by the fd-relative local suite.
- The same matrix covers repository hooks/helpers, submodule gitlinks, LFS pointers, invalid refs, deadline/process termination and every download/unpack/file-count/file-size limit with staging cleanup assertions.
- Secret tests assert token absence from network-visible durable inputs, temporary files after lease close, logs, source/Manifest/run/audit/runner facts and Workspace; stable failure tests assert sanitized external/audit output.
- No product change was needed because every task 9.2 cell already had an independently named executable test from phases 2–4; the release matrix command is now recorded as one gate.
- Verification: the seven-suite Importer matrix passed 146 tests; latest CodeAgent/full regression evidence remains 534 passed + 3 daemon-permission skips / 822 passed + 3 skips.
- Implementation and verification are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 8.1 Completion Evidence

- Added persisted `execution_eligible`, `runner_state`, `runner_network_id` and `cleanup_state` fields with backward-compatible startup migration and explicit run-creation defaults.
- A Code run now becomes routable only after Workspace preparation and runner startup have succeeded and the active execution state/container binding has been committed.
- Terminal cleanup removes the in-memory active route first, then atomically persists terminal status plus execution revocation before invoking runner and Workspace hooks in reverse registration order.
- Successful cleanup clears the persisted container/network bindings and records `removed`/`completed`; cleanup failure remains revoked and records `cleanup_failed`/`failed` without reopening the route.
- Both Code tool and container-runner preflight reject any run lacking active persistent execution eligibility, so no new call can enter after terminal revocation.
- Added parameterized success/cancel/timeout/policy/infrastructure terminal tests that observe revocation during cleanup and prove a subsequent Code tool never reaches the runner; added cleanup-failure persistence coverage.
- Verification: focused lifecycle/runtime/security/runner/migration suite → 60 passed; `pytest -q tests/test_code_agent*.py` → 517 passed; full `pytest -q tests/` → 793 passed; Python compilation and targeted trailing-whitespace checks passed.
- Implementation and verification are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 8.2 Completion Evidence

- Replaced the Workspace manager's hidden 24-hour/minimum-one-hour behavior with the deployment `code_workspace_retention_hours` value, whose default is 168 and whose valid range includes zero for immediate deletion.
- Added an owner-managed nullable Project override that may only shorten the platform limit; run creation freezes the effective minimum into `CodeAgentRun.workspace_retention_hours` with startup migration coverage.
- Retention removes local Git execution metadata, makes the entire per-run root (Workspace plus control facts) read-only and persists `retained_read_only`, a deadline and `workspace_downloadable=false`.
- A zero effective retention safely deletes only the current run root and records `deleted`; artifact id and tool-audit records remain untouched in both retention and immediate-delete paths.
- Runner startup now rejects any Workspace whose persisted state is not `prepared`, closing the retained-directory remount path before Docker allocation.
- Added platform/default/project-minimum calculations, owner/operator permission separation, 168-hour deadline, full-tree permissions, non-downloadability, non-remountability and artifact/audit independence tests.
- Verification: focused retention/workspace/runner/control-plane/migration suite → 95 passed; `pytest -q tests/test_code_agent*.py` → 523 passed; full `pytest -q tests/` → 799 passed; Python compilation and targeted trailing-whitespace checks passed.
- Implementation and verification are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 8.3 Completion Evidence

- Added persistent run cleanup attempt/error/next-attempt fields and startup migrations; new runs initialize the lifecycle retry contract explicitly.
- Added `CodeLifecycleJanitor` with an idempotent one-run cleanup operation, ordered container/network then Workspace cleanup, bounded exponential retry and due-run scanning.
- API lifespan performs one startup-recovery pass before starting a 60-second background janitor; persisted pending/running resources from an API crash are revoked and terminated as infrastructure recovery work.
- Runner containers now carry an immutable run-id label. Recovery deletes a Docker container/network only when its label matches the run, with exact legacy name fallback only when no label exists; missing resources count as already cleaned.
- Runner and Workspace failures persist distinct cleanup errors while emitting the stable `sandbox_cleanup_failed` security alert with only resource type, retry count and cleanup state.
- Added crash recovery/idempotency, Docker deletion failure/retry, real filesystem deletion failure/retry and cross-run container binding mismatch tests; successful retries converge without removing another run's directory/container.
- Verification: focused janitor/lifecycle/runtime/workspace/runner/migration/failure suite → 85 passed; `pytest -q tests/test_code_agent*.py` → 527 passed; full `pytest -q tests/` → 803 passed; Python compilation and targeted trailing-whitespace checks passed.
- Implementation and verification are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 8.4 Completion Evidence

- Added validated snapshot capacity, Workspace capacity and disk low-water deployment limits with production defaults and no coupling to artifact/audit retention.
- Added `StorageCapacityService` metrics from persisted snapshot sizes, actual no-follow per-run directory bytes and filesystem free bytes; each boundary independently controls the aggregate admission result.
- New Code run admission enforces the capacity result as `code_kill_switch_storage_capacity`; the existing operational kill-switch model also supports an explicit `storage:capacity` switch.
- Lifecycle janitor invokes pressure reclaim only when storage admission is closed. Reclaim processes expired retained Workspaces first, then zero-ref snapshots that have no Manifest reference.
- Snapshot deletion still passes through `SnapshotLifecycleService`, which rechecks actual Manifest references atomically; artifact and audit tables are not candidates in either capacity calculation or reclaim.
- Added metrics, three boundary gates, manual storage switch and reclaim-order tests with one published/ref-counted snapshot and one orphan; only the orphan reaches snapshot cleanup.
- Verification: focused storage/config/control-plane/snapshot/janitor/retention suite → 62 passed; `pytest -q tests/test_code_agent*.py` → 534 passed; full `pytest -q tests/` → 811 passed; Python compilation and targeted trailing-whitespace checks passed.
- Implementation and verification are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 9.1 Completion Evidence

- Added an ephemeral no-follow bind probe after Docker inspect validation but before runner facts become active: the API writes a random token inside the exact Workspace, the allocated container must read the same token through `/workspace`, and the API removes it before route activation.
- A wrong daemon host root or Docker-created empty bind now fails as `workspace_mount_invalid` and the just-created container is removed immediately; inspect Source-string equality alone is no longer treated as shared-storage proof.
- Added a real-daemon suite using the locally pinned `python:3.12-slim` digest: API writes a sentinel, the non-root/network-none runner reads and modifies it, then API verifies the changed bytes and inspect facts.
- The real suite also proves wrong host root/empty bind, cross-run identity, extra mounts including Docker socket and nonexistent digest all block startup; existing mock suite continues covering inspect mount/digest/security drift.
- The dedicated runner image build was attempted but Debian arm64 package-index download repeatedly stalled; it was cancelled without creating an image because the bind-path gate needs only the already-local pinned base image. This failure is not hidden as release evidence.
- Verification: escalated real Docker suite → 3 passed; focused runner/workspace/runtime suite → 38 passed; non-privileged `pytest -q tests/test_code_agent*.py` → 534 passed, 3 explicitly skipped for unavailable daemon permission; full `pytest -q tests/` → 822 passed, 3 skipped; Docker post-check found no `code-agent-run1` container; Python compilation and targeted trailing-whitespace checks passed.
- Implementation and verification are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 6.6 Completion Evidence

- Kept `AgentRuntime` and its common task/event/terminal orchestration unchanged; the existing profile compiler continues to attach `CodeToolExecutor` only when a validated Code run produces `CodeExecutionContext`, while Standard Profile keeps the generic executor path.
- Extended bounded per-run Code tool audit facts with `run_id`, `container_id`, frozen `policy_hash` and `policy_version`, retaining the existing tool action, result status, path and output-classification fields without recording tool output or secrets.
- Added a six-tool audit contract covering read/search/edit/git/test/shell and asserting every completed record is bound to the same run, container and frozen policy.
- Added an explicit Standard Profile allocation regression that makes snapshot sealing, Workspace preparation and runner startup fail if called, then proves all three remain uncalled and no snapshot or Code run row is created.
- Verification: `pytest -q tests/test_code_agent_standard_compat.py tests/test_code_agent_runtime_context.py tests/test_code_agent_tools.py tests/test_code_agent_tools_routing.py` → 34 passed; `pytest -q tests -k code_agent` → 489 passed, 276 deselected; full `pytest -q tests` → 765 passed; Python compilation and targeted trailing-whitespace checks passed.
- Implementation and verification are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 7.1 Completion Evidence

- Added frozen `ScanFinding` and `ScanReport` value objects with scanner/version, input hash, complete/status, discovered/scanned byte and file counts, skipped/truncated counts, redacted findings and a stable failure reason.
- Added a strict `passes(expected_input_hash)` boundary requiring complete coverage, zero findings and an exact non-empty input-hash match; a report with findings or hash drift cannot pass even when scanning completed.
- Implemented `BuiltinSecretScanner` with bounded streaming, no-follow file opens, before/after identity checks, deadline enforcement, binary/unsupported/large-file fail-closed handling and sanitized path/classification-only findings.
- Rewired `DefaultSourceScanner` to persist the immutable report without storing matched secret content; `complete` now expresses coverage independently while publish still rejects any finding.
- Added tests for immutable reports, complete text coverage, secret redaction, hash mismatch, binary input, oversized input, unsupported file type, timeout and scanner crash.
- Verification: focused scanner/publish/verifier/artifact suite → 39 passed; `pytest -q tests -k code_agent` → 496 passed, 276 deselected; full `pytest -q tests` → 772 passed; Python compilation, private-symbol call-site audit and targeted trailing-whitespace checks passed.
- Implementation and verification are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 7.2 Completion Evidence

- Added `validate_source_scan_report` as a fail-closed runtime admission gate before `bind_code_run`, Workspace preparation or runner startup.
- The gate requires the run, sealed snapshot and persisted source report to agree on snapshot id/hash/commit and report id; the report must have valid source scope, complete status/coverage, sanitized finding structure and the current scanner name/version.
- The sealed snapshot is rescanned read-only and accepted only when the fresh complete/no-finding report exactly matches the persisted `input_hash`; report drift, missing evidence, scanner failure or invalid findings fail before any writable resource exists.
- Stable failures set the run to `policy_rejected` with only `secret_detected` or `source_scan_failed`; the exception contains no matched bytes, and the shared Agent runtime/model is never entered.
- Added source-gate tests for a valid sealed source, a persisted secret hit and an incomplete scanner report; both failure cases assert zero Workspace prepare, zero runner start and zero AgentRuntime execution.
- Verification: focused source-gate/runtime/publish/scanner suite → 34 passed; `pytest -q tests -k code_agent` → 499 passed, 276 deselected; full `pytest -q tests` → 775 passed; Python compilation and targeted trailing-whitespace checks passed.
- Implementation and verification are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 7.3 Completion Evidence

- Extended the scanner adapter to accept an explicit changed-path set plus bounded virtual inputs, preserving deleted paths as canonical facts and scanning virtual bytes without writing them back into the Workspace.
- `CodeArtifactSealer` now freezes the runner first, recomputes Workspace integrity/changed paths, builds the canonical binary diff, and persists one patch-scope ScanReport covering every changed/new file plus `@canonical.patch.diff`.
- Any patch finding or incomplete report returns a non-success result before artifact creation. The file-only hash is independently recomputed after freeze and must equal the Verifier's recorded content hash, so post-verification input drift cannot pass.
- Added tests for secrets in changed files, secrets present only in deleted canonical-diff lines, untracked binary content, injected scan truncation and post-Verifier content drift; every case asserts no CodeArtifact and no `patch_ready`.
- Verification: focused scanner/verifier/artifact suite → 34 passed; `pytest -q tests -k code_agent` → 503 passed, 276 deselected; full `pytest -q tests` → 779 passed; Python compilation and targeted trailing-whitespace checks passed.
- Implementation and verification are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 7.4 Completion Evidence

- Added canonical serialization for persisted ScanReports and sealed both `source-scan-report.json` and `patch-scan-report.json` into every artifact v2 bundle.
- Artifact admission now requires a complete, zero-finding source report bound to the sealed snapshot, matching snapshot id/hash/resolved commit, a non-empty image digest and a frozen policy hash that exactly matches canonical `policy.json`.
- Manifest v2 binds source/patch report ids and hashes, Verifier report hash, snapshot id/hash, base/resolved commit, image digest/image facts, policy hash and diff hash; the verifier report independently references the patch report id/input hash.
- Read-only bundle loading verifies all six files, every hash, mandatory manifest evidence and cross-file report ids/scopes/coverage/findings before review or download.
- Added integrity tests for all required manifest facts, source/patch report hashes, tampered report bytes and mid-bundle file-write failure; incomplete temporary collections are removed and no sealed row is created.
- Verification: focused result/artifact suite → 23 passed; `pytest -q tests -k code_agent` → 511 passed, 276 deselected; full `pytest -q tests` → 787 passed; Python compilation and targeted trailing-whitespace checks passed.
- Implementation and verification are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 4.1 Completion Evidence

- Extended the CGI request and Manifest response schemas with source type/locator, credential reference, requested ref, trusted image digest, snapshot/security facts and an explicit security-validation object while preserving legacy repository/base-image fields for existing callers.
- Added an owner-only `manifest_options` action whose typed response contains only exact canonical remote origins, approved local roots, trusted image digests and Project-assigned read-only credential metadata/reference; response-model filtering prevents secret/ciphertext fields from entering the API contract.
- Invalid deployment allowlist/root/image configuration produces empty corresponding choices plus fail-closed security readiness rather than a server exception or an unvalidated selectable option.
- Structured draft saves normalize the requested ref and exact repository locator before any mutation, verify Project-scoped credential references and image digests, reject any unauthorized selection atomically, and create/update only a draft `CodeRepositorySource` row.
- Explicitly incomplete structured input remains savable as a repairable draft and uses security schema version 1 to preserve structured-field validation even when every value is blank; published rows remain immutable.
- Reworked the existing Manifest editor so source type, credential reference and image digest are server-option selects; locator/ref inputs surface field-level server errors, approved origin/root hints and security readiness/status without ever displaying or accepting a secret value.
- API tests cover the requested `http://g.testskydata.com/system/dbt-gamestat-ck.git` locator, canonical `:80` origin, reference-only credential metadata, owner isolation, complete/incomplete draft saves and atomic rejection of unauthorized host/type/credential/ref/image selections.
- Verification: focused API/source/local/Secret/config suite passed 113 tests; `npm run build` completed successfully; `pytest -q tests -k code_agent` passed 336 tests with 274 deselected; `pytest -q tests` passed all 610 tests; Python compilation, `git diff --check` and targeted trailing-whitespace checks passed.
- Code, UI and task-scoped tests are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 4.2 Completion Evidence

- `ManifestPublishService.publish()` now runs the full fail-closed pipeline in order: `_validate_policy` (project-owner authorization + manifest draft/immutability + environment tier + source locator/ref/credential/image policy) → `_policy_hash` → `RepositoryAcquirer.acquire` (local copy or guarded remote ref resolve/import) → `SourceScanner.scan` → `SourceSnapshotStore.seal` → transactional freeze (optimistic conflict re-check, snapshot register/attach, admission validation, source activation, audit, commit).
- The router `code_project_post` action=`publish` delegates to `build_manifest_publish_service(db).publish(...)` and maps `ManifestPublishError` to a field-level actionable response, so the API no longer mutates status directly.
- Every pre-transaction failure path (permission, source, image, auth, import, scan, seal) leaves the previously published version untouched, the draft un-published with no `snapshot_id`, and no snapshot with `ref_count > 0`; a sealed snapshot produced before a later failure is preserved as an orphan (`failed`, zero-ref) for the lifecycle janitor rather than left runnable.
- `test_code_agent_manifest_publish.py` covers the strict import→scan→seal ordering + exact freeze, a draft mutated mid-import triggering `manifest_publish_conflict` with orphaned snapshot, and a parametrized fault-injection matrix for permission/source/image/auth/import/scan/seal asserting old-version preservation and no runnable snapshot.
- Verification: `pytest -q tests/test_code_agent_manifest_publish.py` → 9 passed; full `pytest -q tests` → 621 passed; Python compilation and scoped tracked diff checks passed.
- Code, router wiring and task-scoped tests are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 4.3 Completion Evidence

- Added `secure_readiness_reason(db, manifest, *, project, settings)` as the single fine-grained readiness evaluator shared by both `project_availability` (list/detail display) and `create_code_run` (run admission).
- The evaluator re-validates the frozen source/ref/snapshot/image evidence against current deployment state in order: source type/locator presence and allowlist (`repository_source_not_allowed`) → credential reference availability (`repository_auth_failed`) → 40/64-hex commit SHA (`repository_ref_invalid`) → sealed snapshot with matching content hash and resolved commit (`snapshot_invalid`) → trusted image digest membership (`image_digest_invalid`) → Workspace API/host mount readiness (`workspace_mount_invalid`), returning `""` when ready.
- `project_availability` now orders gates as project/environment → manifest presence/republish → structural admission (`manifest_invalid`) → fine-grained readiness, and always attaches `manifest_id`/`manifest_version` once a published Manifest is found so the UI can link the evaluated version even when not ready.
- `create_code_run` keeps structural and kill-switch gates first (so kill-switch and policy-denial behavior is preserved), then the policy contract intersection, then fine-grained readiness before any run row is written; an unready project cannot create a run.
- Added `tests/test_code_agent_readiness.py` covering each distinct reason directly, the ready/empty-source edge cases, and a run-admission contract asserting `project_availability` returns the stable reason while `create_code_run` raises `ManifestUnavailableError(reason)` with zero runs persisted.
- Updated existing synthetic-Manifest fixtures (control-plane, control-plane-UI, policy-layers) to back the frozen evidence with a sealed `CodeSourceSnapshot` and a ready settings view, since admission now requires real snapshot/allowlist/digest facts.
- Verification: `pytest -q tests/test_code_agent_readiness.py` → 14 passed; full `pytest -q tests` → 635 passed (was 621 before this task); no new warnings beyond the existing Pydantic/UTC deprecations.
- Code and task-scoped tests are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 4.4 Completion Evidence

- `create_code_run` now persists a `CodeControlAudit(action="run_create", ...)` fact after the run row, freezing `run_id`, `manifest_version`, `source_id/type`, `requested_ref`, `resolved_commit`, `snapshot_id`, `snapshot_hash`, `image_digest`, `security_schema_version` and `effective_policy_hash` into a sort-key-stable JSON payload (mirroring the existing `manifest_publish` audit pattern in `manifest_publish.py`).
- Run admission already froze the commit/snapshot/digest/policy-hash into the `CodeAgentRun` row and its `task_contract`; the new audit fact makes the same baseline independently observable and traceable without mutating the published Manifest.
- Confirmed `create_code_run` reads only the frozen Manifest/snapshot database fields plus settings and pure policy functions — it never re-resolves a symbolic ref or re-imports from Git — so branch/tag drift or a Git-service outage cannot change an existing Manifest's commit/snapshot or a run's baseline.
- Added `tests/test_code_agent_run_baseline.py` covering: freezing the manifest commit/snapshot without resolving the ref (and without mutating the Manifest); identical baseline across repeated runs with the Git importer patched offline; the `run_create` audit fact recording the frozen commit/snapshot/digest/policy-hash; and an existing run's baseline surviving a simulated commit drift.
- Updated `test_control_plane_writes_are_audited_without_mutating_frozen_runs` to expect the additional `run_create` audit (now `run_create` → `project_update` → `manifest_draft_save`) and assert its frozen facts match the run row.
- Verification: `pytest -q tests/test_code_agent_run_baseline.py` → 4 passed; full `pytest -q tests` → 639 passed (was 635 before this task); Python compilation and `git diff --check` passed.
- Code and task-scoped tests are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 5.1 Completion Evidence

- `WorkspaceManager.prepare(run, *, snapshot_store=None)` no longer clones a remote or resolves a symbolic ref: it verifies the run's immutable snapshot (`snapshot_hash` present + 40/64-hex `resolved_commit` → `workspace_snapshot_identity_invalid`; `snapshot_id == snapshot_hash[:16]`; `SourceSnapshotStore.verify` re-checking content hash + exact commit → `workspace_snapshot_invalid`; sanitized `.git` metadata with no alternates/hooks/remotes), then materializes the worktree and a separate `source.git` (copy of the sanitized `.git`) into a fresh per-run root.
- `prepare` allocates the run root only after verification, marks `_allocated_runs`, and removes the partial root on any later failure; preparation writes `control/baseline.json` (full-tree digest snapshot) and `control/authorized_writes.json`, and records `WorkspaceFacts(baseline_digest, preparation_mode="snapshot_materialize", resolved_commit)`.
- Materialization is pure filesystem: no `subprocess`, `git`, `git_importer` or `secret_store` access. The offline test patches `subprocess.run`, `RestrictedGitImporter.import_remote/import_from_staging` and `DeployTokenSecretStore.resolve_for_importer` to raise and proves `prepare` still succeeds.
- Rewrote `tests/test_code_agent_workspace.py` and added `tests/test_code_agent_workspace_snapshot.py` (7 tests) covering worktree + separate sanitized `.git`, hash/commit/identity mismatch, malformed commit, tampered Git metadata and full offline preparation; updated `test_code_agent_tools.py`, `test_code_agent_verifier.py`, `test_code_agent_artifacts.py` and `test_code_agent_security.py` to seal a real snapshot and pass `snapshot_store=store`.
- Updated the runtime-context compensation-guard test to exercise the new flow: `_verify_snapshot` is stubbed to reach allocation, `_materialize` raises `source unavailable`, and the outer `run_agent` guard marks the run `infrastructure_error`/`startup_failed` with no partial dir and no residual `_allocated_runs` entry.
- Verification: `pytest -q tests/test_code_agent_workspace_snapshot.py tests/test_code_agent_workspace.py tests/test_code_agent_runtime_context.py` → 27 passed; `pytest -q tests -k code_agent` → 372 passed (274 deselected); full `pytest -q tests` → 646 passed (was 639 before this task).
- Code and task-scoped tests are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 5.2 Completion Evidence

- Extended `WorkspaceFacts` with frozen `snapshot_id`/`snapshot_hash` fields and recorded them into `source_facts` during `prepare`, so the run's snapshot identity is part of the recorded workspace facts.
- Extended `WorkspaceIntegrityGuard._validate` with three new fail-closed checks after the existing baseline-digest/commit/repository check: snapshot identity (`workspace_snapshot_identity_invalid`), path/run-id containment (`workspace_cross_run_mount`), and uncontrolled checkout (`workspace_uncontrolled_checkout`); all still mark `run.status = "workspace_integrity_error"`.
- Cross-run mount detection verifies both that the recorded `facts.path` still equals `run.workspace_path` and that the resolved workspace's parent directory name equals `run.id`, so a run can never operate through another run's writable workspace.
- Uncontrolled checkout detection reads the materialized `source.git` HEAD (must be `ref: refs/heads/snapshot`) and resolves `refs/heads/snapshot` against the frozen `resolved_commit`, tolerating `git gc`-packed refs via a loose/packed-ref resolver; moving HEAD or the ref stops the run rather than silently answering status/diff/log against a drifted base.
- Per-run writable directories remain isolated (already guaranteed by `prepare` materializing each run under `root/<run.id>/`); the existing `test_concurrent_runs_never_share_writable_workspace` covers two runs against the same repository.
- Added `tests/test_code_agent_workspace_integrity.py` (6 tests) covering snapshot-hash and snapshot-id drift, cross-run path swap, recorded-path identity mismatch, uncontrolled HEAD checkout, uncontrolled ref move, and the guarantee that an integrity failure never marks the workspace downloadable.
- Verification: `pytest -q tests/test_code_agent_workspace_integrity.py` → 6 passed; `pytest -q tests -k code_agent` → 378 passed (274 deselected); full `pytest -q tests` → 652 passed (was 646 before this task); `git diff --check` passed.
- Code and task-scoped tests are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 5.3 Completion Evidence

- Added `app/services/code_agent/workspace_mount.py` with `resolve_workspace_host_path(workspace_path, *, run_id, snapshot_hash, settings=None)` that maps the API-visible Workspace path to the daemon-host bind source only after fail-closed validation.
- The mapping resolves the workspace strictly under the configured API root and requires the exact layout `<api_root>/<run_id>/workspace`; any missing directory, symlink/`..` escape, cross-run source or guessed path raises `WorkspaceMountError("workspace_mount_invalid")`.
- Added a deterministic per-run sentinel (`sha256(run_id + NUL + snapshot_hash)` written to `<run_root>/.code-agent-run`); the mapping refuses to return a host path unless the sentinel matches the frozen run identity, so a guessed path or a different run's snapshot cannot be mounted.
- The host side is normalized and checked for lexical containment within the configured host root, and host roots containing `..` traversal are rejected.
- `WorkspaceManager.prepare` now writes the run sentinel alongside `control/baseline.json` inside the same cleanup-guarded allocation, binding the run to its frozen snapshot before any runner start.
- `CodeContainerRunner.start` now resolves the validated host path and passes that as the Docker bind source (`volumes={host_path: ...}`); a mapping failure surfaces as `RunnerPolicyError("workspace_mount_invalid")` before the container is created, so the API container path is never used directly as a bind source.
- Updated `tests/test_code_agent_runner.py` to model the dual-root + sentinel setup and assert the bind source is the mapped host path; added `tests/test_code_agent_workspace_mount.py` (10 tests) covering valid mapping, missing roots, outside-root path, cross-run source, `..`/symlink escape, missing/wrong sentinel, host-root traversal and sentinel determinism.
- Verification: `pytest -q tests/test_code_agent_workspace_mount.py tests/test_code_agent_runner.py` → 17 passed; `pytest -q tests -k code_agent` → 388 passed (274 deselected); full `pytest -q tests` → 662 passed (was 652 before this task); `git diff --check` and `openspec validate --strict` passed.
- Code and task-scoped tests are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 5.4 Completion Evidence

- Declared the same-source Workspace dual roots in `deploy/docker-compose.yml` under the API service: `CODE_WORKSPACE_API_ROOT: /app/data/code-agent/runs` (API container view) and `CODE_WORKSPACE_HOST_ROOT: ${GAP_DATA_DIR:-./data}/code-agent/runs` (Docker daemon host view), both sitting under the single `${GAP_DATA_DIR:-./data}:/app/data` bind mount so they name the same on-disk storage.
- Documented the dual-root contract in `deploy/.env.example`, including the "same storage via one bind mount" invariant and that the values normally need no override.
- Added `Settings.code_agent_workspace_mapping_readiness()` — a read-only, side-effect-free mapping probe that validates the API root is absolute and under `data_dir`, the host root is absolute with no `..`, and (when `docker_data_host_path` is known) that the host root's tail under the bind-mount source equals the API root's tail under `data_dir`, emitting `code_config_workspace_roots_not_same_storage` / `code_config_workspace_host_root_not_same_storage` / `code_config_workspace_data_host_path_invalid` on mismatch.
- Wired the probe into `code_agent_security_readiness()` by replacing the former inline root checks with `errors.update(self.code_agent_workspace_mapping_readiness()["errors"])`, so `secure_readiness_reason` still maps `workspace_api_root`/`workspace_host_root` to `workspace_mount_invalid` and the new `workspace_mapping` key surfaces same-storage drift.
- Extended `ensure_dirs()` to create the configured absolute `code_workspace_api_root` (guarded, OSError-safe) so the readiness probe passes at container startup before the first run materializes a workspace.
- Added `tests/test_code_agent_config.py` mapping cases (same-storage ready, different-storage fail, host-root-outside-source fail, traversal data-host-path fail, format-only without `docker_data_host_path`) and `tests/test_code_agent_deploy_config.py` (3 tests) that reads the actual `deploy/docker-compose.yml` and asserts the dual roots share the `code-agent/runs` tail under the one bind mount and that `DOCKER_DATA_HOST_PATH` is exposed for the runtime probe.
- Verification: `pytest -q tests/test_code_agent_config.py tests/test_code_agent_deploy_config.py` → 21 passed; full `pytest -q tests` → 670 passed (was 662 before this task); no new warnings.
- Code, Compose config, readiness probe and deploy-config validation are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 6.1 Completion Evidence

- Added `app/services/code_agent/runner_protocol.py` defining the frozen, versioned runner protocol shared by the API process and the runner image: `RUNNER_PROTOCOL_VERSION = 1` and `RUNNER_HELPER_VERSION = "1"`.
- `RunnerBudgets` is an immutable record of the complete resource budgets (cpu_count, memory_mb, pids_limit, tmpfs_mb, disk_mb, timeout_seconds, output_limit_bytes), each strictly positive and integer-typed.
- `RunnerSpec` is the immutable desired runner configuration: run binding, pinned image digest (`name@sha256:<64hex>` or bare `sha256:<64hex>`), workspace mount source/target, network mode + approved targets, security options (non-privileged, read-only rootfs, cap-drop ALL, no-new-privileges), tmpfs mounts, helper/schema version; deserialization rejects malformed digests, non-positive budgets, network/privilege/rootfs/privilege-escalation escapes and version mismatches.
- `RunnerToolRequest` binds a tool to a run/container with a per-tool parameter schema (read→path; search→query/path; edit→path/content; git→status/diff/log; test→test_index/command; shell→command); unknown tools, illegal parameter fields, missing required parameters, git write operations and version mismatches all raise `RunnerProtocolError`.
- `RunnerToolResponse` carries exit code, bounded stdout/stderr, an explicit `output_limit_bytes`, a `truncated` flag and changed-file facts; an untruncated response whose output exceeds the limit is rejected (`runner_tool_response_output_exceeds_limit`), enforcing bounded output fail closed.
- All three types round-trip `to_dict`/`from_dict` losslessly.
- Added `tests/test_code_agent_runner_protocol.py` (36 tests) covering spec serialization, malformed/missing digest, non-positive/invalid budgets, network/privilege/rootfs/privilege-escalation denials, helper/schema version mismatches, all six valid tool parameter shapes, unknown tool, illegal parameter field, missing required parameter, git write rejection, invalid test selector, missing container binding, bounded-output enforcement and truncated-output acceptance.
- Verification: `pytest -q tests/test_code_agent_runner_protocol.py` → 36 passed; full `pytest -q tests` → 706 passed (was 670 before this task); no new warnings.
- Code and task-scoped tests are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 6.2 Completion Evidence

- Added `app/services/code_agent/runner_helper.py`, a self-contained, stdlib-only program (no `app.*` imports) that is baked into the trusted runner image and executed once per tool call: it reads a single `RunnerToolRequest` JSON line on stdin and writes a single `RunnerToolResponse` JSON line to stdout.
- read/search/edit use Workspace-relative fd containment: every path component is opened with `os.open(..., dir_fd=<parent>, flags=O_NOFOLLOW|O_DIRECTORY)` so absolute paths, `..`/`.` segments and symlinks (including a symlinked intermediate directory) fail closed as `code_path_not_allowed`; intermediate directories are opened relative to their parent fd, never against an absolute join.
- edit performs an atomic temp-file + `os.replace` write into the same resolved directory (with `O_EXCL|O_NOFOLLOW` on the temp and an explicit `follow_symlinks=False` stat rejecting a symlink target), so a write can never flow through a symlink and never leaves a partial file; oversized content is rejected as `code_tool_invalid_input`.
- git is limited to the frozen `status`/`diff`/`log` commands (write operations rejected as `code_git_write_rejected`) run against the sanitized `source.git` via `--git-dir`/`--work-tree`, with a disposable `GIT_INDEX_FILE`, system/global config disabled, hooks disabled and alternates emptied; `read-tree HEAD` materializes the index from the frozen ref.
- test/shell run the validated fixed command through `shlex.split` with `shell=False` (no `-c`), so shell metacharacters are literal arguments rather than control operators — a `>` redirect or `;` separator cannot execute.
- Every output is bounded by `output_limit_bytes` with an explicit `truncated` flag, and command execution is bounded by `timeout` (timeout → `runner_command_timed_out`); the response always carries `helper_version` pinned to `HELPER_VERSION`.
- Added `deploy/code-agent-runner.Dockerfile` that bakes `runner_helper.py` into a minimal `python:3.12-slim` image with git installed, `/workspace` + `/source.git` mount points and the helper as `ENTRYPOINT` (built from the repository root, matching the API image build context).
- Added `tests/test_code_agent_runner_helper.py` (28 tests) covering: version pin vs `RUNNER_HELPER_VERSION`; read within-workspace, absolute-path, `..` traversal, symlink-file and symlink-directory escapes, and large-file truncation; edit atomicity (no leftover temp), absolute/traversal/symlink-target escapes and oversized-content rejection; search scoping and symlink-root escape; git status/diff/log on a real fixture, modified-file status and git-write rejection; shell injection neutralization, command exit-code propagation, timeout fail-closed and large-output truncation; unknown-tool/missing-parameter rejection; and an end-to-end `python -m` stdin→stdout round-trip.
- Verification: `pytest -q tests/test_code_agent_runner_helper.py` → 28 passed; full `pytest -q tests` → 734 passed (was 706 before this task); `git diff --check` clean; `openspec validate code-agent-secure-workspace-runtime --strict` → valid.
- Code, helper image and task-scoped tests are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 6.3 Completion Evidence

- Added the four remaining runner budgets (`pids_limit=256`, `tmpfs_mb=64`, `disk_mb=1024`, `output_limit_bytes=100000`) to `PLATFORM_POLICY["budgets"]` in `control_plane.py`, so the merged effective policy carries the complete `RunnerBudgets` and any lower layer can only tighten them (numeric minimum).
- Added `spec_for_run(run, *, host_path)` to `runner.py` which resolves the frozen run's effective-policy budgets (clamped to platform bounds via `_clamp_int`) and round-trips them through `RunnerSpec.from_dict`, re-validating the pinned digest, security options and every budget.
- `CodeContainerRunner.start` now launches by the digest-pinned image reference (`name@sha256:…` via `_image_reference`) as a non-root user (`user=65534:65534`), with `network_mode=none`, `privileged=False`, `read_only=True`, `cap_drop=["ALL"]`, `security_opt=["no-new-privileges"]`, a single rw `{host_path: /workspace}` mount, and the complete resource bounds: `pids_limit`, `nano_cpus`, `mem_limit`, `tmpfs={/tmp: rw,noexec,nosuid,size=<tmpfs_mb>m}`, `storage_opt={"size": <disk_mb>m}` and the per-run `timeout_seconds` deadline.
- Added `inspect_facts(attrs)` and `verify_inspect_matches(spec, image_id, attrs)` which extract the Docker inspect payload and fail closed (`runner_facts_mismatch`) on any drift in image id, network mode, privileged/read-only-rootfs/cap-drop/no-new-privileges/user, pids/CPU/memory limits, tmpfs size, disk size, or the single rw Workspace mount (source/target/mode/count). `start` removes the container and re-raises on mismatch.
- Extended `RunnerFacts` with the full security/limit facts (pids, tmpfs, disk, timeout, output limit, user, read-only-rootfs, cap-drop, no-new-privileges) with backward-compatible defaults, and recorded them into `run.runner_facts`.
- Added `tests/test_code_agent_runner.py` (13 tests) covering: non-root/digest/bounded-resource launch kwargs (pids/tmpfs/storage-opt), resource mounts/no-network/facts reporting, budget resolution and out-of-range clamping, unsafe-capability denials, exec deadline kill, compensation registration, consistent-inspect acceptance, a 16-case per-fact drift rejection matrix, and container removal on inspect mismatch.
- Updated `test_code_agent_control_plane.py::test_published_manifest_freezes_contract_and_policy_snapshot` to reflect the four additional budget keys in the frozen policy snapshot.
- Verification: `pytest -q tests/test_code_agent_runner.py` → 13 passed; full `pytest -q tests` → 744 passed (was 734 before this task); `git diff --check` clean; `openspec validate code-agent-secure-workspace-runtime --strict` → valid.
- Code and task-scoped tests are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 6.4 Completion Evidence

- Added `CodeContainerRunner.run_tool(run, tool, parameters, *, output_limit_bytes, timeout_seconds)` to `runner.py`, which builds a typed `RunnerToolRequest` (re-validated via `RunnerToolRequest.from_dict`), executes the baked helper (`RUNNER_HELPER_COMMAND = ["python", "/opt/code-agent/runner_helper.py"]`) with the JSON request on stdin and the JSON response on stdout — `client.api.exec_create(stdin=True)` + `exec_start(socket=True)` + `sendall`/`shutdown(SHUT_WR)`/`recv`, no shell concatenation — enforces the per-container deadline, and re-validates the reply via `RunnerToolResponse.from_dict` (fails closed on malformed JSON or helper/version/output-limit mismatch).
- Rewired `CodeToolExecutor` so every approved action is narrowed by `_tool_request` into the exact frozen runner-protocol parameters and dispatched through `self.runner.run_tool`; results are rendered by `_format_result` (test/shell → `{exit_code, output}`, read/search/edit/git → `stdout`), then redacted and audited as before.
- Deleted the API host-filesystem and subprocess branches: the direct `Path.read_text`/`rglob`/`write_text` read/search/edit implementations and the `_git_read` subprocess git query (with its `os`/`subprocess` imports) are gone; there is no host fallback path.
- Helper-level errors are mapped back to the stable rejection taxonomy (`code_file_not_found`, `code_path_not_allowed`, `code_git_query_failed`, …) and `runner_command_timed_out` still promotes to `RunnerCommandTimeout` with the run marked `timed_out`; `edit` content is still validated as a string before routing.
- Added `tests/test_code_agent_tools_routing.py` (4 tests): a monkeypatch suite that arms `Path.read_text`/`write_text`/`rglob` + `subprocess.run` to raise and verifies all six tools complete through a canned `RunnerToolResponse` adapter (asserting the dispatched tool sequence and `tool_calls_used`), plus runner-level tests for request serialization (exact helper command, `stdin=True`, `workdir="/workspace"`, stdin payload fields), malformed-response fail-closed (`RunnerUnavailableError`) and helper-version-mismatch rejection (`RunnerProtocolError`).
- Updated `tests/test_code_agent_tools.py` and `tests/test_code_agent_security.py` to drive the executor through a `run_tool` adapter (delegating to the real in-process `runner_helper` for read/search/edit/git, canned success for test/shell) and to assert on `run_tool` dispatch instead of the removed `runner.exec` calls.
- Verification: `pytest -q tests/test_code_agent_tools_routing.py` → 4 passed; `pytest -q tests/test_code_agent_tools.py tests/test_code_agent_security.py` → 15 passed; full `pytest -q tests` → 748 passed (was 744 before this task); `git diff --check` clean; `openspec validate code-agent-secure-workspace-runtime --strict` → valid.
- Code and task-scoped tests are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 6.5 Completion Evidence

- Added `CodeContainerRunner._preflight(run, container_id)` — a per-call guard invoked at the top of `run_tool` that (a) rejects a run whose `status` is no longer `pending`/`running` (`RunnerUnavailableError("runner_not_active")`), (b) rejects an empty run/container id, (c) rejects a `run.container_id` that does not match the container being executed (`RunnerPolicyError("runner_binding_mismatch")`), and (d) rejects a container already bound to a different run via the runner's `_bindings` registry (cross-run reuse).
- Added `self._bindings: dict[container_id, run_id]` to `CodeContainerRunner`, populated at `start()` (after `run.container_id` is set) and cleared in the `_cleanup` compensation hook and in `freeze()`, so the binding lifetime matches the container lifetime.
- Added `RunnerResourceLimitError(reason)` and `_OOM_EXIT_CODES = {137, -9}`; `run_tool` now raises `RunnerCommandTimeout()` for `runner_command_timed_out`, `RunnerResourceLimitError("runner_oom_killed")` for an OOM exit code, and `RunnerResourceLimitError("runner_resource_limit")` for a helper spawn failure (`error="runner_exec_failed"`), while other helper errors continue to flow to the executor's `CodeToolError` rejection path.
- `CodeToolExecutor.__call__` now maps runner failures to stable terminal states before re-raising: `RunnerCommandTimeout`→`timed_out`/`runner_command_timed_out` (existing), `RunnerResourceLimitError`→`resource_limit_exceeded`/`<reason>`, `RunnerUnavailableError`→`infrastructure_error`/`runner_unavailable`, and `RunnerProtocolError`→`infrastructure_error`/`<reason>`; each records a terminal audit row and, because `status` is no longer `pending`/`running`, the next tool call is rejected with `code_runner_not_active` (subsequent actions stop).
- Added `_ALIASES` entries mapping `runner_oom_killed` and `runner_resource_limit` to `FailureReason.RESOURCE_LIMIT_EXCEEDED` so the persisted specific reasons resolve to the correct external failure presentation.
- Added `tests/test_code_agent_preflight.py` (13 tests) covering: runner-unavailable → `infrastructure_error` + blocked later call; parametrized OOM/pids/canonical resource-limit → `resource_limit_exceeded`; protocol error (output over limit) → `infrastructure_error`; timeout → `timed_out`; inactive-run preflight; cross-run container reuse; stale container fail-closed; OOM exit-code detection; helper-spawn-failure detection; untruncated output-over-limit protocol rejection; and a serialization sanity check that the preflight still routes the exact helper command.
- Updated the three direct `run_tool` tests in `test_code_agent_tools_routing.py` to mark their runs `status="running"` (the preflight now checks active lifecycle), and added a `start()` binding-registration assertion (`runner._bindings["container1"] == "run1"`) to `test_code_agent_runner.py`.
- Verification: `pytest -q tests/test_code_agent_preflight.py` → 13 passed; `pytest -q tests/test_code_agent_tools_routing.py tests/test_code_agent_tools.py tests/test_code_agent_runner.py tests/test_code_agent_security.py` → 45 passed; full `pytest -q tests` → 761 passed (was 748 before this task); `git diff --check` clean; `openspec validate code-agent-secure-workspace-runtime --strict` → valid.
- Code and task-scoped tests are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 3.1 Completion Evidence

- Added a pure, no-I/O remote source parser that only recognizes HTTPS, SSH and explicitly allowlisted HTTP, rejects `file://`, scp syntax, embedded userinfo, unsupported schemes, query/fragment, invalid paths and ambiguous encoded delimiters.
- Canonicalizes scheme and IDNA host case, strips the DNS trailing dot, assigns protocol default ports only when the port is absent, and compares the resulting exact `scheme://host:port` origin against the normalized deployment allowlist.
- Preserves the normalized repository path separately from the origin and marks HTTP sources for the mandatory internal-address gate implemented in task 3.2; task 3.1 itself performs no DNS resolution or network I/O.
- Reused the same allowlist normalizer in deployment readiness so invalid runtime origins cannot pass startup configuration checks.
- Security review found and fixed an explicit port-zero bypass caused by truthy fallback; leading/trailing whitespace, port `0`, default-port variants, mixed case/trailing-dot hosts, embedded/encoded credentials and path encoding variants now have regression coverage.
- Includes the requested test repository form `http://g.testskydata.com:80/system/dbt-gamestat-ck.git` as an explicitly allowlisted HTTP normalization case without making a network request.
- Verification: focused source/config/failure suite passed 73 tests; `pytest -q tests -k code_agent` passed 249 tests with 273 deselected; `pytest -q tests` passed all 522 tests; Python compilation and scoped tracked `git diff --check` passed.
- Code and task-scoped tests are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 3.2 Completion Evidence

- Added a Repository network guard that resolves immediately before every connection and returns only pinned, validated IP strings for the transport; no hostname-only approval or DNS result cache is trusted.
- Every DNS answer must pass: loopback, link-local, metadata, multicast, unspecified, reserved and unapproved private/special results deny the whole connection so a transport cannot choose a weaker answer.
- Added explicit administrator internal-CIDR configuration, restricted to RFC1918/IPv6 ULA subnets; HTTPS/SSH may use global public addresses or approved internal addresses, while HTTP additionally requires an exact HTTP origin and an approved internal address.
- Added a bounded controlled redirect loop that re-applies origin normalization, DNS resolution and IP policy before each request; cross-allowlist redirects and same-host DNS rebinding fail before the transport sees the unauthorized target.
- Redirect source escape maps to stable `repository_network_policy_denied`; ordinary initial source denial remains the task 3.1 source reason, and resolver outages remain the stable unreachable reason.
- Added malicious resolver/redirect tests for mixed answers, loopback/link-local/metadata/private targets, rebinding on repeated connections, same-host and cross-host redirects, public HTTP, approved internal HTTPS/SSH/HTTP and redirect bounds; connector-call assertions prove unauthorized targets are not requested.
- Deployment readiness rejects HTTP origins without approved internal CIDRs and rejects broad, loopback, link-local or malformed CIDR configuration; internal network configuration is covered by the platform-admin resource boundary.
- Verification: focused network/source/config/failure/authorization suite passed 113 tests; `pytest -q tests -k code_agent` passed 277 tests with 273 deselected; `pytest -q tests` passed all 550 tests; Python compilation and scoped tracked `git diff --check` passed.
- Code and task-scoped tests are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 3.3 Completion Evidence

- Added canonical local repository/root parsing that rejects relative paths, `file://`, `..`, repeated/ambiguous path segments, arbitrary host paths, direct source symlinks and sources outside all administrator roots.
- Local source data is copied to a newly allocated Importer staging directory; the result contract explicitly reports `source_bound=false`, and post-import source mutations do not alter staged content.
- Traversal starts from a frozen approved-root directory descriptor and opens every directory/file relative to its parent with `O_NOFOLLOW`; source and staging roots must be disjoint.
- Root identity is frozen by device/inode/type, each opened entry is matched against no-follow scan facts, cross-device entries are denied, files are re-statted after reading, and directory listings are compared after recursive copy.
- Symlinks and non-regular entries are rejected before content reads; every failure removes partial staging.
- Added deterministic race coverage that replaces a scanned file with an outside symlink and replaces the entire approved root after Importer initialization; both fail before outside content reaches staging.
- Added path traversal, arbitrary outside path, source/nested symlink, FIFO, staging-id escape, staging/source overlap, legitimate new repository and copy independence tests.
- Verification: focused local-source/config suite passed 28 tests; `pytest -q tests -k code_agent` passed 293 tests with 273 deselected; `pytest -q tests` passed all 566 tests; Python compilation and targeted trailing-whitespace check passed.
- Code and task-scoped tests are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 3.4 Completion Evidence

- Added restricted Git import limits wired directly from platform Settings for download bytes, unpacked bytes, file count, single-file bytes and deadline.
- Every Git process receives an allowlisted environment and command-level isolation for system/global configuration, hooks, credential helpers, fsmonitor, recursive submodules and LFS filters; deploy-token-shaped ambient environment values are not inherited.
- Controlled local acquisition uses a no-hardlink/no-checkout clone with an empty template, resolves a validated ref to an exact 40/64-hex commit, enumerates the exact tree, and materializes it only through a bounded Git archive.
- Remote HTTP(S) acquisition re-runs the task 3.2 network guard immediately before clone, disables Git redirect following and pins Host/SNI to an approved IP using `http.curloptResolve`; the requested test repository URL is covered without making a live request.
- Remote SSH acquisition fails closed without an absolute non-symlink administrator known-hosts file and otherwise uses strict host checking, canonical HostKeyAlias and the approved pinned IP without inheriting user SSH configuration.
- Submodule gitlinks, LFS pointer content, symlinks/special tree entries, invalid refs and unsafe archive paths are rejected; no checkout/smudge/submodule initialization occurs.
- Git child processes, object storage, command output and archive extraction are checked against the shared deadline/limits; active timeout kills the child, and all ref/feature/resource/filesystem failures remove partial staging.
- Added real Git fixture tests for exact ref import, hooks, credential helper/config isolation, gitlinks, LFS, download/unpack/file/single-file limits and cleanup, plus controlled remote HTTP pinning, denied-IP no-start and SSH host-key tests.
- Verification: focused Git/network/failure/config suite passed 86 tests; `pytest -q tests -k code_agent` passed 314 tests with 273 deselected; `pytest -q tests` passed all 587 tests; Python compilation and targeted trailing-whitespace check passed.
- Code and task-scoped tests are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 3.5 Completion Evidence

- Added a same-filesystem snapshot store that copies Importer output into a private temporary directory, sanitizes it, computes a deterministic SHA-256 content address, writes seal metadata and atomically renames it to the final hash path.
- Removed hooks, all remotes, credential helpers, alternates, modules, reflogs, transient heads and every non-frozen ref; the only remaining ref is `refs/heads/snapshot` at the exact resolved commit.
- Replaced repository config with a minimal offline configuration, rebuilt the index from the exact commit and pruned unreachable objects while retaining history needed by status/diff/log.
- Snapshot hash covers every worktree and retained Git file plus executable/read-only mode; seal metadata binds hash, exact commit and schema version, while immutable-tree checks reject permission-only tampering.
- Descendants are made read-only before hashing; the final root becomes read-only immediately after atomic publication. Verification rejects missing/changed metadata, bytes, modes, symlinks/special entries and containment escape.
- Same exact source/commit imports deduplicate to one verified path; an existing content-addressed destination is never reused until its full hash and seal metadata pass.
- Added tests proving no remote/hooks/helper/alternates/moving refs, exact HEAD, clean status, usable diff/log in a materialized copy, deterministic dedup, content/config/seal tamper detection and permission-only tamper detection.
- Full-suite concurrency exposed Git's temporary pack-index rename; the monitor now tolerates only transient disappearing entries while keeping all other I/O failures fail-closed.
- Verification: focused Git/snapshot suite passed 27 tests; `pytest -q tests -k code_agent` passed 321 tests with 273 deselected; `pytest -q tests` passed all 594 tests; Python compilation and targeted trailing-whitespace check passed.
- Code and task-scoped tests are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 3.6 Completion Evidence

- Added persistent snapshot cleanup attempts, stable error and next-attempt fields with fresh-schema and existing-table startup migration coverage.
- Registered sealed snapshots only after full store verification; content-hash dedup rows are reactivated safely and cleanup state reset without creating a second snapshot row.
- Manifest attachment verifies the snapshot again, increments `ref_count` using an atomic database expression inside the caller's transaction, is idempotent for the same attachment, and atomically decrements a replaced snapshot reference.
- Publish transaction rollback reverts both the Manifest fields and reference increment; post-rollback orphan marking can only claim rows with `ref_count=0 AND NOT EXISTS(manifest)`.
- Janitor deletion rechecks actual Manifest references and performs the same atomic zero-ref/no-Manifest claim before touching files, serializing safely against in-flight publish updates; referenced snapshots are repaired/protected rather than deleted.
- Verified deletion checks hash/commit/containment before making a snapshot writable for removal; failures persist attempt count, stable cleanup error and exponential next-attempt, then converge idempotently on retry.
- Abandoned `.seal-*` staging cleanup is cutoff-based so active seals survive, and content-addressed directories are never selected by the staging janitor.
- Added stale-session dual-publish tests proving no lost increment, transaction failure/orphan tests, referenced-snapshot protection, replacement decrement, cleanup failure/backoff/retry and migration tests.
- Verification: focused lifecycle/snapshot/schema/startup suite passed 22 tests; `pytest -q tests -k code_agent` passed 327 tests with 274 deselected; `pytest -q tests` passed all 601 tests; Python compilation, scoped tracked `git diff --check` and targeted trailing-whitespace check passed.
- Code and task-scoped tests are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 2.1 Completion Evidence

- Added organization scope to User/CodeProject with backward-compatible startup migration and user-admin assignment API; projects inherit the creator organization.
- Added built-in owner/operator/reviewer roles and startup reconciliation so the authorization model is deployable on both fresh and existing databases.
- Added a centralized service authorization module for platform security configuration, project ownership, run operation and artifact review; lower roles cannot inherit platform management rights.
- Required subject-aware authorization inside direct run creation and artifact acceptance/review services, and enforced the same gates at Project/Manifest and chat API entries including cancellation.
- Denials use stable non-disclosing reasons and persist sanitized `authorization_denied` audits; public visibility does not grant sensitive CodeAgent operations.
- Added cross-organization, cross-project, low-role, admin-resource, direct-run, cancellation and audit-redaction tests; migrated legacy ACL-only tests to explicit role semantics.
- Verification: focused task suite passed 93 tests; `pytest -q tests -k code_agent` passed 153 tests with 273 deselected; `pytest -q tests` passed all 427 tests; targeted `git diff --check` passed.
- Bare `pytest -q` is not valid repository evidence because collection executes `scripts/smoke_test.py`, which requires a separately running HTTP server; this was recorded and the formal `tests/` suite passed completely.
- Code, migration, service/API gates and task-scoped tests are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 2.2 Completion Evidence

- Added strict fail-closed secret decryption so CodeAgent credentials never use the legacy plaintext fallback.
- Added the Secret Store-owned encrypted Deploy Token model with read-only/status constraints, organization scope and approved Project assignments; schema upgrade/downgrade coverage includes the new table.
- Added admin-only assignment/disable/metadata methods and owner-only Project metadata listing that returns references and non-secret metadata, never ciphertext or token values.
- Added an Importer service identity and context-managed zeroizing lease; fake identities, missing/disabled/corrupt references, cross-organization or cross-project access all fail as `repository_auth_failed`.
- Assignment validates that every target Project exists in the declared organization; resolution revalidates the Project scope before decryption.
- Added persistence, log, audit, error, runner-facts and Workspace leak tests, including lease cleanup when audit persistence fails and a database constraint rejecting write-capable credentials.
- Verification: focused task suite passed 30 tests; `pytest -q tests -k code_agent` passed 164 tests with 273 deselected; `pytest -q tests` passed all 437 tests; targeted `git diff --check` passed.
- Code, schema, Secret adapter and task-scoped tests are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 2.3 Completion Evidence

- Replaced ambiguous positional policy merging with explicit platform→organization→project→Manifest→Profile→task keyword layers.
- Added independent Project policy persistence and startup migration; Manifest policy now contributes exact frozen source id/type, image digest, paths, tools and budgets.
- Allowed paths, tools, shell commands, sources, source types and image digests merge by intersection; network uses logical AND; protected/test-integrity paths use union; every numeric budget uses the minimum.
- Added deterministic `policy_sources` facts for fields and individual budgets so the persisted effective policy identifies the layer that imposed the active limit.
- Lower-layer expansion attempts cannot widen the effective result; invalid/unknown fields and boolean/unknown budgets fail closed.
- Manifest validation and run admission reject when the actual frozen source, source type, image, allowed paths or tools falls outside the resulting intersection.
- Added six-layer, all-budget, path/tool/source/network/image, protected-union, shell-command, invalid-shape, frozen-run and denied-contract tests.
- Verification: focused task suite passed 75 tests; `pytest -q tests -k code_agent` passed 182 tests with 273 deselected; `pytest -q tests` passed all 455 tests; targeted `git diff --check` passed.
- Code, migration, policy merge/admission gates and task-scoped tests are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 2.4 Completion Evidence

- Added stable `FailureStage` and `FailureReason` enums covering authorization, source allow/unreachable/limit, auth, ref, snapshot, image, mount/integrity, source/patch scan, runner policy/unavailable, resources/budgets, Verifier and cleanup.
- Added legacy internal-reason aliases and fixed actionable external messages; unknown internal exception/provider text maps to `infrastructure_error` and is never echoed.
- Added centralized security failure audits linking actor, project, run, policy hash, stage, stable reason and trace id.
- Audit facts are allowlist-based and secret-shaped values are redacted/dropped; URL, repository, stdout/stderr, error, password/token/credential fields cannot enter the payload.
- Migrated authorization denials and Secret resolution failures to centralized security audits; normalized result API failure payloads while keeping successful/terminal presentation semantics.
- Added every-stage mapping, legacy alias, actionable response, malicious audit payload, sanitizer and unknown-result-text leak tests.
- Verification: focused task suite passed 55 tests; `pytest -q tests -k code_agent` passed 212 tests with 273 deselected; `pytest -q tests` passed all 485 tests; targeted `git diff --check` passed.
- Code, taxonomy, external mapping, audit integration and task-scoped tests are complete; progress evidence is recorded before updating the formal task checkbox.

### Task 1.4 In Progress

- Added idempotent startup migration from insecure `published` rows to `security_republish_required` after secure columns exist.
- Added explicit admission/readiness detection and secure evidence requirements before a Code run can be created.
- Upgraded ready test fixtures to carry snapshot/scan/image evidence and added legacy admission/migration coverage.
- Initial full control-plane UI run exposed 3 legacy draft assumptions; tests now prepare secure evidence before publish and the rerun passed (30 tests).
- Verification: complete Phase 1 related suite passed (70 tests); Standard Agent regression included; targeted `git diff --check` passed.
- Remaining warnings are existing Pydantic/UTC deprecations and did not affect results.
- Code and task-scoped tests confirmed complete; progress evidence recorded before the formal checkbox update.

### Task 1.3 In Progress

- Added deploy settings for exact repository allowlists, local roots, importer hard limits, snapshot/Workspace retention, dual Workspace roots and trusted image digests.
- Added a non-throwing startup readiness validator so incomplete security config disables CodeAgent admission without breaking Standard Agent/API startup.
- Added focused valid/missing/escape/symlink/digest/limit configuration tests.
- Verification: targeted config/startup/control-plane suite passed (32 tests); targeted `git diff --check` passed.
- Code and task-scoped tests confirmed complete; progress evidence recorded before the formal checkbox update.

### Task 1.1 In Progress

- Added SQLAlchemy models for structured repository sources, scan reports and sealed source snapshots with lifecycle/check constraints and reference-only credential storage.
- Added an idempotent upgrade and reverse-order downgrade migration module, wired into startup without changing existing migration branches.
- Added focused schema tests for upgrade/downgrade, forbidden secret-value columns, valid references and invalid lifecycle values.
- Files modified/created: `apps/api/app/models.py`, `apps/api/app/startup.py`, `apps/api/app/services/code_agent/schema_migration.py`, `apps/api/tests/test_code_agent_secure_schema.py`.
- Verification: `pytest -q tests/test_code_agent_secure_schema.py tests/test_startup_migrations.py` passed (6 tests); targeted `git diff --check` passed.
- Code and task-scoped tests confirmed complete; progress evidence recorded before the formal checkbox update.

### Task 1.2 In Progress

- Extended Manifest and Code run persistence with frozen source/ref/commit/snapshot/scan/image/schema fields; run additionally stores a deterministic effective-policy hash.
- Extended the frozen task contract and run creation copy path while preserving legacy field fallback until task 1.4 enforces republishing.
- Added startup ALTER coverage for existing Manifest and run tables and expanded contract immutability assertions.
- Added an actual draft→publish→run-copy test plus a legacy-table startup migration test for every new frozen field.
- Verification: targeted control-plane/schema/startup suite passed (31 tests); targeted `git diff --check` passed.
- Code and task-scoped tests confirmed complete; progress evidence recorded before the formal checkbox update.

### Errors

| Error | Resolution |
|---|---|
| 严格校验判定 Verifier 原 scenario 被遗漏 | 恢复原 scenario 名称；不削弱新增源码/patch 扫描语义 |
| Task 1.4 full control-plane UI suite: 3 legacy draft publish assumptions failed | Keep secure evidence gate; update those tests to model a prepared secure draft before publish, then rerun |
| Task 2.1 first regression run: 7 failures exposed legacy ACL-only authorization assumptions | Keep fail-closed service RBAC; migrate affected fixtures to explicit roles and generalized denial reasons, then rerun |
| Task 2.1 call-site audit regex used unsupported look-around | Use simple `rg` call listings plus syntax/test validation instead |
| Task 2.1 focused check used repository-root paths while cwd was `apps/api` | Re-ran with `app/...` and `tests/...`; syntax check passed |
| Repository-root pytest collection included live-server `scripts/smoke_test.py` and failed its sandboxed HTTP connection | Run the formal `tests/` suite explicitly; keep the external smoke script out of task 2.1 evidence |
| Task 2.3 shell-policy search used a repository-root glob from `apps/api` cwd | Re-ran with `rg` cwd-relative path and `-g`; no file changes resulted from the failed search |
| Task 3.1 scoped `git status` used repository-root file arguments while cwd was `apps/api`, so it returned no entries | Re-run from repository root with the same explicit paths; Python compilation already succeeded independently |
| Task 3.2 first focused run reused one `tmp_path` with a helper that intentionally creates its root once | Give the missing/approved readiness cases separate child roots; no product-code failure was involved |
| Task 3.4 call-site check again used a repository-root path while cwd was `apps/api`, and chained the test behind it so the test did not run | Re-run the call-site check with cwd-relative paths, then run pytest as a separate command |
| Task 3.5 first snapshot run: test fixture assumed hooks directory existed, and macOS denied renaming a temporary directory after its own mode was set read-only | Create the malicious hook directory explicitly; hash only executable semantics, make descendants read-only before atomic rename, then make the published root read-only |
| Task 3.5 first full suite: Git atomically renamed a temporary pack-index file between `scandir` and `stat`, causing the concurrent download monitor to report a false unreachable failure | Treat only transient `FileNotFoundError` as an expected disappearing temp entry; all other monitor I/O failures remain fail-closed |
| Task 3.6 inspection used `apps/api/routers/code_project.py` instead of the actual `apps/api/app/routers/code_project.py` path | The required model/migration output was already obtained; use the correct app-relative router path for any follow-up inspection |
| Task 4.1 first UI patch used a stale duplicate-line context and did not apply | Re-read exact anchors and applied the UI change in small, independently verified patches |
| Task 4.1 first focused run: 2 new-draft tests found an unmaterialized SQLAlchemy integer default (`None`) | Treat transient model defaults explicitly with `(value or 0)`; rerun passed all 113 focused tests |
| Task 4.1 first legacy UI assertion still expected `repository` instead of structured `source_locator` | Updated the form contract assertion and added server-option/reference-only UI coverage; no product behavior was weakened |
