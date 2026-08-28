# Findings & Decisions: code-agent-secure-workspace-runtime

## Confirmed Scope

- 仓库导入器是唯一允许在 Code runner 外获取源码的边界；运行期 Code Tools 全部在每 run 专用容器内。
- 远程仓库支持 HTTPS/SSH 与管理员 allowlist 内的内网 HTTP；测试目标为 `http://g.testskydata.com/system/dbt-gamestat-ck.git`。
- 仓库凭据来自 Secret Store 的只读 Deploy Token 引用，不进入 URL、Manifest、Workspace、容器、模型上下文或日志。
- Manifest 发布时解析 branch 并冻结 commit SHA；Code run 不跟随移动分支，也不在运行期访问 Git 服务。
- 本地仓库只能来自管理员配置的只读根目录，经受控快照导入；禁止任意宿主路径、符号链接和路径逃逸。
- 仓库来源需防 SSRF、重定向逃逸、未授权网段、超时与资源耗尽；V1 明确拒绝 submodule、Git LFS 和超限仓库。
- 所有 read/search/edit/git/shell/test 必须通过受限 runner 在容器 Workspace 内执行；API 只做授权、调度与审计。
- Compose 部署必须把 API 容器内 Workspace 正确转换成 Docker daemon 可见的宿主 bind 路径，并有真实 Docker 集成验证。
- 容器默认断网、只读根文件系统、最小 capability 与资源限制；测试数据库优先使用隔离 ClickHouse sidecar，不直接连接业务环境。
- 导入和封存阶段执行秘密扫描；无法完整扫描时 fail closed。
- 容器终态立即销毁，Workspace 默认保留 7 天，sealed artifact/Verifier/审计长期保留。

## Existing-System Facts

- 当前 Workspace 每 run 独立，但 clone 发生在 API 进程。
- 当前 `code_test`/`code_shell` 在容器内；`code_read/search/edit/git` 仍由 API 进程直接操作宿主 Workspace。
- 当前 Compose 路径转换疑似缺失：Code runner 直接将 API 容器路径交给 Docker daemon。
- 当前 repository 是未区分 source type、credential reference 或 allowlist 的普通字符串。
- `CodeContainerRunner` 已具备断网、只读 rootfs、cap-drop、no-new-privileges 和基础 CPU/内存限制，但只把 API 可见的绝对 Workspace 路径直接交给 Docker volume API，且镜像仍按 tag 读取。
- `CodeToolExecutor` 的 `code_test`/`code_shell` 已调用 runner；`code_read`/`code_search`/`code_edit` 使用 Python 文件 API，`code_git` 使用 API 进程的 subprocess，确认当前是混合执行。
- `CodeProjectManifest`、`FrozenCodeTaskContract` 与 `CodeAgentRun` 仍只保存 `repository`、`base_commit` 和镜像字符串，尚无 source/snapshot/credential-ref/image-digest 结构。
- Workspace 当前默认保留 24 小时，并在每 run 目录内保留 `source.git` 到终态；目标设计需要改为已封存快照输入、运行期无远端和默认 7 天不可执行保留。
- Task 1.1 inspection: project uses SQLAlchemy models in `apps/api/app/models.py` and startup-driven schema migration rather than an Alembic directory.
- Existing CodeAgent tables use string IDs/timestamps and JSON serialized into Text columns; new task 1.1 models should match this established style and add only reference identifiers/metadata, never secret material.
- `init_db` currently uses `Base.metadata.create_all` plus targeted forward ALTER statements; there is no Alembic/down framework. Task 1.1 therefore needs a narrowly scoped secure-workspace schema migration module with idempotent upgrade and explicit reverse-order downgrade for its new tables.
- `models.py` and `startup.py` already contain substantial user/previous-change edits. Task 1.1 must add only the new imports/models/migration hook and preserve every surrounding modification.
- Task 1.2 inspection: `FrozenCodeTaskContract` and `create_code_run` currently copy only legacy repository/base commit plus policy JSON; Manifest publish simply changes status after existing validation.
- To preserve ordered implementation, task 1.2 will add/freeze secure fields while retaining fallback reads for legacy rows; task 1.4 is the formal step that will mark legacy published Manifests `security_republish_required` and block their admission.
- Task 1.3 inspection: `Settings` has no CodeAgent security bundle or validation method; startup calls `ensure_dirs` and `init_db`, so readiness validation can be added without aborting Standard Agent startup.
- Missing/invalid secure configuration should return a stable not-ready result rather than throw during global API startup; project admission can consume that result in later readiness tasks.
- Task 1.4 inspection found only two shared test helpers constructing published Manifests; both can be upgraded to secure evidence without broad fixture churn, while a dedicated legacy row will cover republish-required behavior.
- Legacy status migration must run after new columns are added and must be idempotent; admission should explicitly detect `security_republish_required` instead of collapsing it into `manifest_missing`.

## Artifact Decisions

- Repository Source 与 Sandbox Runtime 是新的独立外部行为契约，分别建立 `code-agent-repository-source` 和 `code-agent-sandbox-runtime` capability。
- `code-agent-workspace` 现行“终态清理可写 Workspace”要求与已确认的 7 天取证保留需要协调：终态先撤销执行能力并立即移除容器，Workspace 变为不可执行保留态并在到期后清理。
- Skill 加载与使用验证不会通过模糊任务混入本 change；proposal 已明确将其排除。
- `code-agent-control-plane` 保持业务配置与角色授权语义；敏感值只以 Secret 引用出现。
- `code-agent-project-policy` 负责跨层策略交集与稳定失败原因；`code-agent-sandbox-runtime` 负责真正的执行边界与部署挂载门禁，避免重复定义实现责任。
- 源码扫描发生在创建可写 runner 之前，patch 扫描发生在 `patch_ready` 之前；任一阶段结果不完整均 fail closed。

## Scope Exclusions

- 不在本 change 实现 Skill revision/hash、只读 Skill bundle 或 Code run Skill 加载；归入 `code-agent-skill-context`。
- 不在本 change 创建使用文档、UI 帮助入口或 `dbt-gamestat-ck` 黄金案例；归入 `code-agent-usage-validation`。
- 不增加自动 commit、push、PR 或原仓库写入。

## Resources

- `openspec/specs/code-agent-workspace/spec.md`
- `openspec/specs/code-agent-project-policy/spec.md`
- `openspec/specs/code-agent-verification/spec.md`
- `openspec/specs/code-agent-control-plane/spec.md`
- `openspec/changes/archive/2026-08-23-code-agent-v1/`
- `openspec/changes/archive/2026-08-23-code-agent-control-plane-ui/`

## Tooling Notes

- 一次使用未匹配的 zsh glob 搜索 Compose 文件失败；后续改用 `rg` 的 include/exclude 规则，不依赖 shell glob。

## Task 2.1 Authorization Inspection

- 现有通用授权只提供管理员角色、页面 permission 与 `visibility/allowed_users/creator` 资源可见性；尚未确认存在组织域、CodeProject 操作角色或服务层统一授权器。
- Code Project router 当前自行组合页面权限和 `can_use_code_project`，Artifact sealing service 未接收授权主体；task 2.1 需要继续逐个核对 source、Secret、Manifest、run 与 artifact review 的入口，避免仅在 UI/router 隐藏操作。
- 代码库没有 organization/tenant 数据模型；`User` 与 `CodeProject` 都未持久化组织归属，因此现状无法满足规格要求的 subject + organization + project + resource 同时校验。
- 现有 `allowed_users` 仅表达项目 ACL，没有区分 owner/operator/reviewer；普通 `user` 一旦在 ACL 中即可编辑 Manifest、创建 run、读取/下载并接受 artifact，确认存在 task 2.1 要消除的横向角色提升。
- `CodeRepositorySource` 已由 `project_id` 归属项目，Manifest、run、artifact 也都有 project scope；可用最小模型扩展是在 `User`/`CodeProject` 增加组织归属，并以平台 admin、项目 creator(owner)、ACL+operator role、ACL+reviewer role 组合成集中式服务授权门禁。
- Task 2.1 采用角色与范围正交的最小模型：`master/admin` 是平台管理员；creator 或同组织 ACL 内的 `owner` 管理项目；同组织 ACL 内的 `operator` 操作 run；同组织 ACL 内的 `reviewer` 读取/接受 artifact。`visibility=public` 不授予敏感 CodeAgent 操作。
- 为真实执行跨组织校验，`User` 与 `CodeProject` 增加 `organization_id`，历史行启动迁移为 `default`；项目内资源继续通过既有 `project_id` 强制匹配，避免为每张资源表复制组织字段。
- 新的集中授权服务统一返回脱敏的 not-found/unauthorized reason 并写入不含资源内容的 `authorization_denied` 审计；router 和 run/review 服务正在接入相同门禁。
- Task 2.1 最终接入点包括：Project/Manifest GET/POST、Code run create/cancel、result/verifier/artifact read/download/accept，以及未来 source/local-root/Secret/image 管理入口可直接复用的 admin resource gate；直接调用 `create_code_run` 也必须显式提供 actor。
- 内置角色目录新增 owner/operator/reviewer，并由 startup 对旧库补齐；用户管理 API 可设置组织归属，项目创建时冻结创建者组织，旧 User/CodeProject 行迁移到 `default`。
- 拒绝响应不会返回项目、run、artifact 或敏感配置详情；内部 `authorization_denied` 审计只记录 actor、operation 和 authorized project scope，不记录秘密或跨项目资源 id。

## Task 2.2 Secret Reference Inspection

- 代码库目前只有通用 Fernet `encrypt_secret`/`decrypt_secret` 与各资源自管的 encrypted column，没有独立 Secret Store 或 Deploy Token reference adapter。
- 现有 `decrypt_secret` 在解密失败时返回原值以兼容历史明文字段；CodeAgent Secret adapter 不能复用该宽松语义，否则损坏或伪造的 ciphertext 可能被当作凭据使用，必须提供 strict decrypt/fail-closed 路径。
- Repository source/Manifest 已只保存 `credential_ref`，满足引用侧模型基础；task 2.2 需要新增 Secret Store 自有加密表、只读 metadata/assignment API 语义，以及只对最小 Importer service identity 开放的短时 lease。
- Importer 尚在 Phase 3，因此 task 2.2 的边界应是可注入的 resolver/lease adapter；实际 Git 认证通道、argv/env/config 隔离由 3.4 接入，但当前测试仍需证明 adapter 本身不向 persistence/log/audit/error/runner facts/Workspace 泄露值。
- Task 2.2 最终新增 Secret Store 自有 `CodeDeployCredential` 加密记录；Source/Manifest 仍只保存 `credential_ref`，Code run 不复制凭据引用或值。
- Deploy Token assignment 必须为只读、目标 Project 全部存在且属于声明组织；owner 只能读取本组织/本项目可见的 metadata/reference，operator 等低角色不能读取 metadata。
- Secret resolution 只接受单例 Repository Importer service identity，所有 missing/disabled/wrong-scope/corrupt 情况统一为 `repository_auth_failed`；strict Fernet 解密不继承历史明文 fallback。
- 解析结果使用可清零 `DeployTokenLease`，包括审计提交失败在内的所有离开路径都执行清零；成功/失败日志、审计、异常、runner facts、Workspace 和业务元数据均只保留非秘密引用/事实。

## Task 2.3 Policy Merge Inspection

- 现有 merger 从固定平台基线开始，只接受未命名的 organization→project/profile/task 位置参数；Project 没有独立 policy 字段，Manifest policy 与 Project policy 被合并成同一层，因此无法证明六层顺序。
- 当前 allowed paths/tools/shell/network 与 budget 扩权请求直接抛错，而 delta spec 的交集语义要求有效策略排除扩张部分、网络取逐层 AND、预算取最小值；protected/test-integrity paths 已采用并集，方向正确。
- 当前有效策略尚未冻结 source/source type/image digest allowlist，也没有限制来源 provenance；task 2.3 需要显式六层 named API、Project policy persistence 和确定性限制来源事实。
- 新 merger 已采用 named platform/organization/project/manifest/profile/task 层：allowlist/path/command 取交集、network 逐层 AND、protected/test-integrity 取并集、所有预算取最小值，并在 `policy_sources` 冻结实际限制来源。
- Project 新增独立 policy 持久化与旧库迁移；Manifest 层强制加入 frozen source id/type、image digest、allowed paths/tools/budgets，避免这些执行事实只存在于旁路字段。
- 审计复核发现仅计算空交集还不够：若实际冻结 source/image 被有效策略排除但 run 仍启动，会绕过 policy；create/readiness 必须在 merge 后校验实际契约仍属于有效交集。
- Task 2.3 最终在 Manifest validation 与 run admission 都执行 frozen contract membership gate；source id/type、image digest、paths 或 tools 的交集为空/不包含实际值时，在 Workspace/runner 创建前稳定拒绝。
- 六层输入改为 keyword-only，消除了位置参数把 Manifest/Profile 层错位的风险；未知字段、非法类型、布尔伪装数值预算均 fail closed。

## Task 2.4 Failure and Audit Inspection

- 现有 CodeAgent 失败原因散布在 control plane、Workspace、runner、tools、Verifier、artifact 与 lifecycle 中，且 result API 直接回显 `run.failure_reason`；若底层把 provider/exception 文本写入该字段，会形成外部泄漏面。
- 现有授权与 Secret Store 审计分别手写 JSON，没有统一 stage/reason/policy hash/trace id 契约；runner/scan/cleanup 等后续组件也缺少可复用的脱敏事件构造器。
- Task 2.4 新增稳定 `FailureStage`/`FailureReason` taxonomy、旧内部原因 alias、固定可行动外部文案和 allowlist-based audit facts；未知内部文本统一降级为 `infrastructure_error` 而不是回显。
- 集中审计事件固定关联 actor、project、run、policy hash、stage、reason 和 trace id；URL、error/stdout/stderr、credential/password/token 等非 allowlist 或 secret-shaped 值被丢弃。
- 现有 authorization denial 与 Secret resolution failure 已接入集中安全审计，result serialization 已改用稳定外部 failure payload；后续 Source/runner/scan/cleanup phases 可直接复用同一枚举和 recorder。

## Execution Tracking Initialization

- Planning with Files 将从 proposal artifact 生成记录切换为 implementation execution tracking；它不复制或重新定义 OpenSpec requirements。
- `openspec/changes/code-agent-secure-workspace-runtime/tasks.md` 是唯一正式任务来源；phase 仅引用 task 编号范围。
- 单个 OpenSpec task 的完成顺序固定为：实现完成 → 对应测试/验收通过 → 更新 `progress.md` 记录证据 → 最后将 `tasks.md` 对应 checkbox 勾选。
- 当前初始化阶段不授权 implementation，也不会预先勾选任何 OpenSpec task。

## Apply Session 2026-08-23

- OpenSpec apply state is `ready`, schema is `spec-driven`, and CLI progress is 0/41.
- CLI contextFiles include proposal, six delta specs, design and tasks; all must be read before implementation.
- No additional `context` or `operationGuidance` was returned by the CLI.
- Dynamic instruction is to work through pending tasks and pause on blockers or clarification; no task may be narrowed or deferred silently.
- Proposal/design re-read confirms Source→Snapshot→Workspace separation, publish-time immutability, typed runner protocol, dual-root mount mapping, persistent janitor and complete ScanReport are controlling implementation decisions.
- Control-plane/project-policy/repository-source specs re-read confirms secret references only, exact source allowlists, stable readiness/failure reasons and run admission before any writable resource.
- Sandbox/verifier/workspace specs re-read confirms all six tools are container-only, source scan precedes writable runner, patch scan precedes `patch_ready`, runner cleanup is immediate and Workspace retention defaults to seven days.
- `tasks.md` re-read confirms 41 tasks remain unchecked and task 1.1 is the only authorized starting point.

## Artifact Execution Facts

- Artifact execution dependency is Source/Secret/config schema → controlled Importer and sealed snapshot → publish/readiness → snapshot Workspace and daemon path mapping → typed all-container tools → dual-stage verification → persistent cleanup → integrated release gates.
- Repository acquisition is allowed only in the controlled Importer; published Manifest freezes exact commit, snapshot and image digest, while Code runs operate without Git credentials or Git server access.
- Runtime completion requires all six Code Tools to execute in the per-run container with no API-host fallback; container inspect facts and real Docker bind-path identity are release evidence, not optional diagnostics.
- Source and patch scanning must produce complete coverage reports; unsupported, truncated, timed-out or failed scans are non-success outcomes.
- Existing Project/Manifest configuration must expose only Secret references and must surface stable readiness reasons without disclosing sensitive values.
- Task 3.1 patch is present as a standalone pre-connect boundary: it performs URL parsing/canonicalization and exact origin matching without DNS or network I/O; task 3.2 remains responsible for resolved-address and redirect-hop enforcement.
- The initial task 3.1 implementation recognizes only `https`, `ssh`, and explicitly allowlisted `http`, rejects userinfo/query/fragment/ambiguous encoded delimiters, and canonicalizes omitted ports to protocol defaults before exact allowlist comparison.
- Security review found that `port or default_port` would treat explicit port `0` as absent and could make it match the default-port allowlist; defaulting must test `port is None`, and both runtime policy and deployment readiness now share this exact validation boundary.
- Task 3.2 needs a second independent deployment value for approved internal CIDRs: an exact HTTP origin allowlist alone cannot prove that the host resolves into an administrator-approved internal range.
- The safe connection contract must return pinned, already-validated IP addresses and re-run resolution on every connection/redirect hop; validating a hostname and then letting a lower client resolve it again would leave a DNS-rebinding gap.
- HTTP is constrained to explicitly approved internal ranges, while HTTPS/SSH may use public global addresses or explicitly approved internal ranges; mixed DNS answers fail as a whole so the transport cannot choose an unvalidated address.
- Administrator-approved internal CIDRs are intentionally limited to RFC1918 IPv4 and IPv6 ULA subnets; loopback, link-local, metadata, multicast, unspecified and reserved targets remain hard-denied and cannot be reopened by a broad CIDR.
- Initial source allowlist failures retain the task 3.1 source reason, but redirect source/origin failures are remapped to `repository_network_policy_denied`, matching the repository-source SSRF scenario.
- Task 3.3 cannot safely use `Path.rglob`, `shutil.copytree` or bind mounts for an untrusted local source because each pathname lookup can follow a replaced component; the copy boundary must traverse from an approved root directory fd with `O_NOFOLLOW` and compare inode/device metadata before and after reads.
- Local import staging must be disjoint from the source tree, newly allocated per import, and removed on every policy/race failure; the returned contract explicitly identifies a copied staging path rather than the original repository path.
- Approved local-root identity must be frozen when the Importer is constructed, not merely re-statted just before open; otherwise replacing the approved directory with a new directory at the same pathname would evade the intended approval boundary.
- Root approval freezes object identity (device/inode/type), not mutable timestamps, so repositories may legitimately be created under the same approved root after service initialization while wholesale root replacement is still rejected.
- Import staging and approved source roots must be disjoint trust domains; allowing either root to contain the other could make partial copies appear as source input or recursively include importer output.
- Task 3.4 can avoid executing repository-defined filters and checkout hooks by cloning with `--no-checkout` into an empty template and materializing the exact resolved commit via a bounded `git archive` stream; submodule gitlinks and LFS pointer files are rejected before the output becomes eligible for sealing.
- Git process isolation requires an allowlisted environment plus command-line overrides for system/global config, hooks, credentials, fsmonitor, recursive submodules and LFS filters; importer staging is cleaned on every ref, feature, resource or deadline failure.
- Resource deadlines must be checked after child exit and during archive extraction as well as while polling a running Git process; otherwise a command or large file that crosses the boundary between checks could be accepted late.
- The deployed Git 2.50 client exposes `http.curloptResolve`, so HTTPS/HTTP acquisition can keep the canonical hostname for Host/SNI while pinning the socket to the IP approved by task 3.2; Git redirects remain disabled to prevent an unvalidated extra hop.
- SSH acquisition must not inherit ambient host-key behavior: it is admitted only with an absolute, non-symlink administrator known-hosts file and an ephemeral command that uses strict checking, the canonical HostKeyAlias and the validated pinned IP.
- Remote import now consumes the task 3.2 guard directly immediately before clone. HTTP(S) keeps redirects disabled and pins one already-approved address; SSH fails closed without managed host keys and does not inherit user SSH configuration.
- Private credential delivery remains outside Git argv/environment in this task; the task 2.2 Secret lease will be connected at the publish orchestration boundary rather than weakening the isolated Git process contract.
- Task 3.5 snapshot identity must cover both worktree content and retained sanitized Git metadata; hashing only the commit/tree would not detect reintroduced remotes, hooks, helpers or alternates.
- Sealing should occur in a temporary directory under the snapshot store so the final content-addressed rename is same-filesystem and atomic; an existing destination is reusable only after a full hash and seal-metadata verification.
- Snapshot verification must include immutable permission state as well as bytes: descendants are normalized to read-only before hashing, file modes participate in the hash, and a separate immutable-tree check covers directories and the excluded seal metadata file.
- Task 3.6 must treat `ref_count` as an atomic cache, never the sole deletion authority: janitor deletion is permitted only after a fresh Manifest count in the same service operation confirms zero references.
- Manifest attachment must increment with a database expression inside the caller's publish transaction and be idempotent for an already-attached snapshot; rollback therefore reverts both Manifest fields and the count together.
- Janitor deletion requires an atomic row claim with both `ref_count = 0` and `NOT EXISTS` over Manifest references. This serializes against the publish transaction's atomic increment and prevents the filesystem deletion window from racing an in-flight attachment.
- Publish-failure orphan marking uses the same zero-ref/no-Manifest atomic predicate; if an in-flight concurrent publish wins, orphan marking returns protected rather than overwriting the committed reference cache.
- Task 4.1 should preserve legacy draft fields for compatibility but treat the structured Source row, requested ref, credential reference and image digest as the secure control-plane contract; complete explicit selections are validated at save time, while genuinely incomplete drafts remain savable with field-level status.
- Project-facing option APIs may expose exact approved origins, local roots, trusted image digests and assigned Secret metadata/reference only; they must never resolve or serialize the Secret value.
- Task 4.1 uses a typed `ManifestOptionsResponse` as an output allowlist, so adding a field to Secret Store metadata cannot silently expand the project-facing API; malformed source/root/image settings remove the affected choices and preserve the readiness error.
- Pydantic `model_fields_set` distinguishes a structured editor submission from an old legacy-field caller without changing legacy behavior; a structured draft records security schema version 1 so an all-empty draft remains in structured validation mode.
- Source type, credential reference and image digest can be constrained to server-owned selects, while the locator remains free-form by necessity and therefore receives the same exact canonical allowlist validation again at save time.
- Task 4.1 testing found that SQLAlchemy column defaults are not materialized on a transient object before flush; security schema arithmetic must tolerate `None` rather than relying on the mapped integer default.
- `tasks.md` contains 41 unchecked tasks in 9 ordered groups; initialization found no pre-completed task that can be checked from planning evidence alone.
- Execution phases map one-to-one to OpenSpec task groups: Phase 1→1.1–1.4, Phase 2→2.1–2.4, Phase 3→3.1–3.6, Phase 4→4.1–4.4, Phase 5→5.1–5.4, Phase 6→6.1–6.6, Phase 7→7.1–7.4, Phase 8→8.1–8.4, Phase 9→9.1–9.5.
- Later phases are dependency gates, not alternative requirements; implementation must follow the checkbox order unless a discovered dependency is recorded here and reflected only as an execution-order adjustment.
- Existing Planning with Files directory was reused and converted from proposal tracking to execution tracking; creating a second plan directory would split execution history and was therefore avoided.

## Task 6.6 Unified Runtime Inspection

- The shared `AgentRuntime` already owns task identity, event publication, LLM loop, terminal handling and persisted messages for both profiles; Code Profile specialization is injected through `AgentContext.tool_executor` and the outer Code lifecycle wrapper rather than a second ReAct engine.
- Standard Profile is distinguishable by the absence of `CodeExecutionContext` and `tool_executor`; this is a stronger resource-allocation boundary than checking only the profile string.
- Existing Standard compatibility tests covered default profile and event-envelope behavior but did not prove snapshot/Workspace/runner non-allocation; task 6.6 now makes those constructors/operations forbidden in the Standard regression.
- Existing Code tool audit recorded action/status/path but omitted the frozen run/container/policy binding required by the sandbox-runtime scenario; the audit can add those non-secret identifiers without changing execution behavior or storing tool output.

## Task 7.1 Scanner Inspection

- The previous source scanner reused `scan_changed_content` and set ORM `complete=false` when a secret was found, so it could not distinguish complete coverage with findings from incomplete coverage; downstream gates need these states separated.
- A scan report is acceptable only through one strict predicate: complete coverage, zero findings and an exact expected input-hash match. Callers must not infer acceptance from status or findings alone.
- Binary, oversized and unsupported inputs are coverage gaps, not clean inputs. They retain enumeration statistics and a deterministic report hash but remain incomplete regardless of whether the inspected prefix contains a secret.
- Scanner findings need only path and classification for policy decisions; matched bytes are unnecessary and would create a persistence/log leakage surface.

## Task 7.2 Source Scan Admission Inspection

- A report-id reference alone does not prove that a run will execute the bytes that were scanned; runtime admission must bind report→sealed snapshot→run and compare a fresh read-only scan hash before creating writable resources.
- Source scan rejection must occur before `bind_code_run` and Workspace allocation. This keeps the failure outside the active tool route and makes “runner was never started” directly testable.
- Persisted finding rows are treated as an allowlisted schema (`path`, `classification`) and their count must match `findings_count`; malformed or expanded payloads fail as scanner evidence corruption rather than being exposed.

## Task 7.3 Patch Scan Inspection

- Scanning only current changed files is insufficient: deleting a secret removes it from the Workspace but leaves the secret in canonical diff deletion lines. The canonical diff must therefore be a first-class scan input.
- The final patch report needs both the changed/new file bytes and canonical diff bytes, while Verifier input-drift comparison needs the file-only hash. Keeping these as two explicit checks avoids conflating artifact bytes with Workspace identity.
- Runner freeze must precede the authoritative patch report; otherwise a still-active tool route could mutate bytes between scan and artifact sealing.

## Task 7.4 Artifact Evidence Inspection

- Storing only ScanReport database ids in an artifact is not independently verifiable after database loss or export. The serialized source and patch reports must be sealed as artifact files and addressed by manifest hashes.
- `policy_hash` must equal the canonical frozen policy bytes, not merely copy an arbitrary run field; the sealer now checks the run hash before writing any bundle.
- Artifact review must verify both per-file hashes and cross-file relationships (Verifier→patch report, manifest→both report ids). A collection of individually hashed but unrelated files is not a coherent sealed artifact.

## Task 8.1 Persistent Lifecycle Inspection

- In-memory active-route removal alone is insufficient because a stale worker can still hold a run object and container id; every tool and runner call must re-check persisted execution eligibility and runner state.
- Binding the chat route before Workspace/runner allocation creates an avoidable admission window. The route is now published only after container facts and persistent active state commit successfully.
- Terminal status and execution revocation form one authorization decision and must commit before any potentially slow or failing Docker/Workspace cleanup hook runs.
- Cleanup hooks remain reverse ordered so runner removal precedes Workspace retention/deletion; failed cleanup records a durable failure state but never restores execution eligibility or the active route.
- The current hardened runner uses Docker `network_mode=none`, so no temporary network is normally allocated; the persisted network binding is nevertheless cleared with the container binding during terminal cleanup.

## Task 8.2 Workspace Retention Inspection

- Deployment configuration already declared a 168-hour Workspace default, but `WorkspaceManager` silently used 24 hours and clamped zero to one; retention behavior must consume the same platform value admitted by readiness.
- Project retention is a lifecycle setting rather than an execution-tool policy capability. A dedicated nullable Project field preserves “inherit platform”, while every run freezes the effective minimum for deterministic cleanup.
- Read-only retention must cover the entire per-run root, not only `workspace/`; otherwise control metadata remains writable and can weaken retained-state evidence.
- File permission changes alone do not prevent a privileged Docker daemon from mounting retained bytes read-write. Runner admission must reject every persisted Workspace state except `prepared` before container allocation.
- Workspace deletion and long-term artifact/audit retention are separate state transitions; cleanup code only mutates Workspace path/state/deadline/downloadability and leaves artifact/audit fields unchanged.

## Task 8.3 Lifecycle Janitor Inspection

- Process-local cleanup hooks are useful for the normal path but cannot recover after interpreter termination; run rows need their own retry counters and deadlines just as snapshot rows already do.
- Startup recovery and periodic cleanup have different authority: startup may declare persisted pending/running executions abandoned, while an ordinary periodic pass must not terminate a currently live active run.
- Docker id alone is not adequate deletion authority. New resources carry a run-id label, and janitor fallback to the deterministic legacy name is allowed only when the label is absent; a mismatched label is never overridden by a matching name.
- A missing Docker resource is an idempotent success, whereas daemon errors and identity mismatches are retryable failures. This distinction lets retries converge after a previous deletion succeeded but the database commit did not.
- Runner and Workspace cleanup failures share the public `sandbox_cleanup_failed` taxonomy but retain a sanitized `resource_type` fact and distinct persistent cleanup error for operations.

## Task 8.4 Storage Capacity Inspection

- Storage pressure has three independent dimensions: logical snapshot bytes, per-run Workspace bytes and physical free bytes. A single aggregate byte counter cannot express the safe admission boundary.
- Workspace size measurement follows no symlinks and deduplicates run roots; snapshot size uses the sealed lifecycle row so metrics do not need to traverse shared immutable content on every admission.
- Low-water pressure closes only new Code run admission. It does not terminate existing Standard Agents or mutate published Manifests.
- Reclaim ordering is a security property: expired per-run retained state is disposable first; only then may a snapshot with both zero cached refs and no actual Manifest reference enter the existing atomic snapshot cleanup service.
- Long-term artifacts and audits are deliberately absent from capacity reclaim candidates, preserving the lifecycle separation established in the design.

## Task 9.1 Real Docker Bind-Path Inspection

- Docker inspect can prove which host path string was requested, but cannot prove the API and daemon resolve that string to the same storage; Docker may create a missing wrong-root directory and still report a perfectly matching mount.
- A short-lived file probe inside the only allowed Workspace mount closes this gap without adding another mount or leaving bytes in the repository diff. It must complete before the active route is published.
- On the local Docker Desktop daemon, the pinned non-root container successfully modified the API-created file with default temporary-directory permissions; no test-only permission widening was necessary.
- The dedicated runner image build depends on Debian package metadata for Git and can be an environmental release prerequisite distinct from bind identity. The real bind gate used the already-cached pinned Python base rather than claiming the stalled runner-image build succeeded.

## Task 9.5 Release Runner Inspection

- The original build failure was specific to the default Debian package endpoint: Aliyun, Tencent and TUNA release mirrors all answered the same trixie metadata probe successfully.
- Build-time Debian mirror arguments preserve official portable defaults while allowing a release environment to choose a reachable package path; this removes an environmental blocker without changing the runtime trust model.
- A release trust value must be the built image's RepoDigest, not a tag or a fabricated local image id. Compose accepts the exact digest through `CODE_TRUSTED_IMAGE_DIGESTS`; the repository intentionally keeps the fail-closed empty default rather than committing a machine- and build-specific digest.
- The exact built digest passed both content inspection (Git plus baked helper) and the real runtime's non-root/read-only/no-network/cap-drop inspect checks, including startup cleanup recovery with no leaked labeled containers.


## Continuation findings (2026-08-25)

- Patch sealing must distinguish secret findings (`findings_count`) from unscannable binary classifications; treating any `findings` tuple as `secret_detected` rejected binary fixtures with the wrong stable reason.
- Global `_cleanup_hooks` keyed by shared test `run1` caused order-dependent `CodeCleanupError` in lifecycle persistence; tests must isolate module state / run ids.
- Sandbox pytest runs are not reliable evidence for this change (PermissionError on `.git` teardown inflates failure counts).
