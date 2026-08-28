## Context

见 [proposal.md](./proposal.md) 的动机与范围。当前 CodeAgent 已在 ReAct-Engine 中形成统一 run，但 `WorkspaceManager.prepare()` 仍由 API 进程执行远程 clone；`CodeToolExecutor` 仅把 test/shell 交给 `CodeContainerRunner`，read/search/edit/git 仍直接操作 API 文件系统或 subprocess。runner 已具有部分容器加固，却直接把 API 可见路径作为 Docker bind source，且 Project/Manifest 仅以普通字符串保存 repository、base commit 和 image。

该 change 同时跨越控制面、持久化、仓库网络入口、Workspace、Docker runtime、Code Tools、Verifier 和清理任务。设计必须在 Docker Compose 的“API 容器路径 != Docker daemon 主机路径”条件下工作，并保证 Standard Agent 路径完全不经过这些组件。

## Goals / Non-Goals

**Goals:**

- 建立唯一、可审计、fail-closed 的源码获取路径，运行期不携带仓库凭据或访问原 Git 服务。
- 把所有 Code Tool 的文件系统和命令执行统一收口到每 run runner，不存在 API host fallback。
- 使已发布 Manifest 冻结 source snapshot、commit、image digest 和有效策略，运行事实可复现。
- 让容器、Workspace 与长期工件具有分离的生命周期和可恢复清理状态。
- 保留 ReAct-Engine 的任务身份、事件、预算和终态编排，并保持 Standard Profile 行为不变。

**Non-Goals:**

- 不在 runner 中加载或冻结 Agent Skill；后续 `code-agent-skill-context` 使用本 change 提供的只读 snapshot/runtime 边界。
- 不实现真实仓库黄金任务、ClickHouse 验证环境、操作文档或 UI walkthrough；这些属于 `code-agent-usage-validation`。
- 不增加向原仓库写入、自动 commit/push/PR、通用联网或生产环境执行能力。

## Decisions

### 1. 将源码生命周期拆为 Source、Snapshot、Workspace 三层

控制面保存结构化 `RepositorySource`：`type`、脱敏 locator、请求 ref、可选 `credential_ref` 和来源策略引用。发布服务调用独立 `RepositoryImporter`，后者在临时 staging 中获取源码、解析 commit、执行限制与源码扫描，再原子封存为 content-addressed `SourceSnapshot`。已发布 Manifest 只引用 snapshot id、精确 commit SHA、扫描报告和 importer policy/version；run 只从 sealed snapshot 物化 Workspace。

远程 Git 获取仅发生在 Importer。凭据通过 Secret Store adapter 临时解析，使用不进入 argv、环境持久化或 Git config 的认证通道，完成后立即销毁；Importer 输出中不包含 remote credential。远程快照保留实现只读 Git 操作所需的最小、已净化对象/元数据，删除 remote、hooks、credential helper、alternates 与工作树外引用。本地来源先 `realpath` 到管理员根目录内，再以不跟随 symlink 的遍历方式复制到 staging，绝不把原路径直接 bind 给 runner。

选择 sealed snapshot 而不是每 run clone，是因为它把分支解析、认证、SSRF 与 Git 服务可用性从执行路径移出，并让相同 Manifest 的 run 获得相同输入。代价是需要 snapshot 存储和回收；通过 content hash 去重、引用计数与独立 retention 控制。

### 2. Source policy 在发起连接前完成，Importer 不信任 Git 默认网络行为

来源解析器先拒绝 embedded credentials、`file://` 和非批准 scheme，再按规范化后的 scheme/host/port 匹配精确 allowlist。连接 adapter 对每次 DNS 解析和目标 IP 做网段判定；HTTP 自动重定向默认关闭，仅可由 adapter 逐跳重新校验后继续。SSH 使用同样的解析目标策略和管理员托管 host-key policy。内部 HTTP 必须同时命中精确 host:port allowlist 与批准内网范围，不能因“内网”概念获得通配权限。

Git hooks、递归 submodule、LFS smudge/filter 和外部 credential/config 均禁用。Importer 在下载、解包和遍历阶段分别计量网络字节、对象/展开字节、文件数、单文件大小和 deadline；任何不确定状态删除 staging 并产生稳定失败原因。

仅靠 URL regex 或在 API 内直接运行 `git clone` 无法覆盖 DNS rebinding、重定向和资源耗尽，因此不采用。实现可以是隔离 worker/container 或受限 service process，但必须遵守相同 Importer 接口、网络策略和审计契约；部署形态不是 capability 的一部分。

### 3. 发布是唯一把 mutable ref 转为 immutable contract 的事务边界

草稿可保存不完整 source/ref 配置，但 publish 按顺序执行：权限检查 → source/secret/image policy 校验 → Importer resolve/import → source scan → snapshot seal → 数据库事务冻结 Manifest。只有 snapshot 和扫描报告已封存后才能提交 published version；失败不得替换当前 published Manifest。

`CodeProjectManifest` 增加 source id/type、credential reference、requested ref、resolved commit、snapshot id/hash、image reference/digest、source scan report/version 和 security schema version。`CodeAgentRun` 再复制冻结值与有效策略 hash，避免运行时 join 到已变化草稿。秘密值不进入任何表。

既有 `repository`/`base_commit` 字段在兼容期只用于展示和迁移，不能作为安全 runtime 输入。选择重新发布而不是默默把既有 URL 当作可信来源，是因为历史数据没有 allowlist、credential provenance、snapshot scan 和 digest 证据。

### 4. Code Tools 使用统一的 runner protocol，不复用 API 文件 API

`CodeToolExecutor` 保留输入 schema、策略 preflight、预算与审计职责，但所有已批准操作转换为 typed `RunnerToolRequest`，由绑定 run/container 的 runner adapter 执行。runner image 内提供固定版本的 tool helper，通过 JSON/stdin 或等价无 shell 拼接协议实现：

- read/search/edit 在 Workspace 内执行 fd-based 路径 containment、大小限制和原子写入；
- git 仅允许 status/diff/log，并使用 snapshot 物化的净化本地 Git 元数据；
- test/shell 只执行冻结 validation plan 或完整 allowlist 命令，拒绝运行时拼接；
- 响应包含 exit code、bounded stdout/stderr、变更事实和 helper version，API 再进行脱敏与审计。

runner adapter 每次校验 run/container 绑定与 active lifecycle state；容器不可用、响应无效或 helper 版本不匹配时直接失败。保留 API host fallback 会使同一个工具在不同安全域产生不同语义，因此删除原来的 Path/subprocess 分支，而不是为其加 feature fallback。

### 5. runner 启动配置由冻结策略生成并进行事实校验

`RunnerSpec` 是平台、组织、项目、Manifest、Profile 和任务策略交集后的不可变值，包含 image digest、CPU、memory、pids、disk/tmpfs、deadline、output limits、network targets 与 mounts。数值预算统一取最小值，集合权限取交集，保护项取并集；审计同时记录 requested 与 effective 值及限制来源。

容器按 image digest 启动，使用非 root user、read-only rootfs、cap-drop ALL、no-new-privileges、默认 network none，仅挂载当前 Workspace 为 rw，并提供有界 tmpfs。Docker socket、Secret Store 和其他宿主目录从 spec 类型上不可表达。若后续验证需要 sidecar，必须由平台创建隔离网络且 Manifest 只声明批准服务身份；本 change 不提供任意目标网络。

启动后读取 Docker inspect facts，与 RunnerSpec 比对 image id、network、mount source/target/mode、security options 和 limits；不匹配即移除容器并失败。此处不依赖模型或容器内自报。

### 6. Compose bind path 使用双根映射，禁止猜测

部署增加两个独立配置：API 视角的 `workspace_api_root` 与 Docker daemon 主机视角的 `workspace_host_root`。对 run Workspace 先解析相对于 API root 的安全相对路径，再拼接到 host root；两端都执行规范化、根目录 containment、run-id/state 标记与 inode/sentinel 身份校验。只有映射验证通过的 host path 可传给 Docker API。

不直接复用通用 `docker_data_host_path` 推导，也不把 `/app/data/...` 原样交给 daemon，因为两者都可能静默挂载空目录或错误 run。部署 readiness 执行只读映射探针；真实 Docker 集成测试必须从 API root 写入随机 sentinel、由 runner 读取/修改，再由 API root 验证同一文件，同时断言错误 root 与跨 run path 被拒绝。

### 7. 生命周期以显式状态和幂等清理协调

run 资源分开建模：snapshot 是不可变共享输入；runner/container 是短生命周期执行资源；Workspace 是每 run 可写资源；sealed artifacts/audit 是长期记录。终态协调器先原子撤销 run 的 `active` 执行资格，再停止/删除 runner 与临时网络，然后把 Workspace 转为 `retained_read_only`（默认 168 小时）或立即删除。保留态不注册 tool route、不持有 container/secret/network，且 `workspace_downloadable=false`。

后台 janitor 使用状态与 deadline 执行幂等删除，成功记为 `deleted`，失败记为 `cleanup_failed` 并带重试计数/下一次时间；进程启动时恢复未完成 cleanup。不能只依赖进程内 callback，因为 API 崩溃会遗留资源。sealed artifact 和审计采用独立 retention，不随 Workspace 删除。

### 8. 秘密扫描必须产生可验证 coverage，而非单一布尔值

Scanner adapter 对 SourceSnapshot 和 canonical diff/新增文件分别输出不可变 `ScanReport`：scanner/version、input hash、枚举文件/字节数、已扫描/跳过/截断计数、findings 的脱敏位置、结束原因与完整性结论。只有 `complete=true && findings=0 && input_hash` 匹配当前输入才通过。

源码报告在 runner 创建前检查；patch 报告在 Verifier/sealer 冻结 Workspace 后检查。二进制、超限、格式不支持、timeout、scanner crash 或 coverage 不一致均返回非成功，不把“没有发现”解释成安全。报告可以长期保留，但 finding 内容与日志必须脱敏。

### 9. 权限与失败原因在边界入口统一校验

平台管理员管理 source allowlist、本地 roots、Secret metadata/assignment 和 trusted image；Project owner 只能引用对其可见的批准项并发布 Manifest；operator 只能启动/取消有权项目的 run；reviewer 只能读取有权工件。每个服务方法与 API 入口都检查 subject + organization + project + resource scope，后台 job 使用最小化 service identity。

失败原因使用枚举并按阶段映射，例如 source not allowed/unreachable/auth/ref/limit、snapshot scan、image digest、workspace mount/integrity、runner policy/resource、patch scan/verifier 和 cleanup。外部响应只提供脱敏 detail；内部审计关联 policy/hash、actor、stage 与 trace id。

## Risks / Trade-offs

- [Git transport 的 DNS、redirect 与 SSH 行为复杂，错误封装可能重新引入 SSRF] → 将连接控制集中到 Importer adapter，默认禁止 redirect，逐连接测试 IP policy，并用恶意 DNS/redirect 测试服务验证。
- [Content-addressed snapshot 与 7 天 Workspace 保留增加磁盘占用] → snapshot hash 去重、发布引用计数、导入/运行磁盘硬限额、janitor 指标和低水位停止开关。
- [把 read/edit/search 移进容器会增加每次调用延迟] → 复用每 run 常驻 runner 与单一 typed helper；不以 host fallback 换取性能。
- [只读 chmod 不能单独构成保留态安全边界] → 终态先撤销路由并删除容器/网络；保留目录不再挂载到任何执行资源，chmod 仅作纵深防御。
- [镜像 digest 与 snapshot schema 升级使旧 Manifest 不兼容] → 明确 security schema version 和 readiness reason，要求重新发布而非隐式转换。
- [真实 Docker 测试在无 daemon 的 CI 中不可运行] → 保留纯单元测试，但把 Docker suite 设为具备 daemon 环境的强制发布门禁，不能用 mock 结果替代。
- [秘密扫描误报阻断运行] → 提供受审计的规则版本管理和重新发布流程；V1 不提供任务级绕过。

## Migration Plan

1. 先增加 nullable source/snapshot/digest/scan/lifecycle 字段、状态枚举和新表/索引；旧字段继续可读，旧已发布 Manifest 标记 `security_republish_required`。
2. 部署 Secret reference adapter、管理员 source/image 配置、snapshot store、Importer 与 scanner；在不启用新 run 前执行来源/扫描/泄密测试。
3. 配置 Compose 的 API/host Workspace roots 和可信 image digest，运行 readiness probe 与真实 Docker bind-path suite；未通过则保持 Code run admission 关闭。
4. 切换 publish 流程生成 sealed snapshot，并要求目标项目重新发布 Manifest。已存在 pending/running Code run 在切换窗口前排空或取消，不做新旧 runtime 混跑。
5. 切换全部 Code Tool 到 runner protocol，删除 API Path/subprocess 执行分支，启用 runner inspect gate、双阶段扫描和持久化 lifecycle janitor。
6. 通过单元、契约、数据库迁移、恶意来源、私有凭据脱敏、真实 Docker、并发隔离、Verifier 与清理恢复测试后开放项目 admission；Standard Agent regression suite 必须保持通过。

回滚时先启用 CodeAgent 全局停止开关并清空/终止 active runner，再回退应用服务；保留新表与字段以避免丢失 snapshot/audit 事实，不把新 Manifest 降级成旧 URL clone。只有旧 Standard Agent 路径可继续运行，Code run 在安全 runtime 恢复前保持关闭。
