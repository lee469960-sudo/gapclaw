## 1. 数据模型与安全配置基线

- [x] 1.1 增加结构化 Repository Source、sealed Source Snapshot、scan report 引用以及 source/snapshot 生命周期数据模型与数据库迁移，并通过迁移 up/down 与模型约束测试验证秘密值没有持久化字段。
- [x] 1.2 扩展 CodeProjectManifest 与 CodeAgentRun，冻结 source id/type、requested ref、resolved commit SHA、snapshot id/hash、image digest、security schema version 和 effective policy hash，并通过创建草稿、发布版本和 run-copy 契约测试验证不可变字段一致。
- [x] 1.3 增加来源 allowlist、本地只读 roots、Importer 限额、snapshot/Workspace retention、Workspace API/host 双根和可信 image digest 配置，实施启动时格式与根目录校验，并通过合法/缺失/逃逸配置测试验证 fail-closed readiness。
- [x] 1.4 实施既有 Manifest 兼容迁移，将没有安全 snapshot/digest 证据的历史已发布版本标记为 `security_republish_required` 且禁止新 run，并通过迁移前后 admission 回归测试验证 Standard Agent 不受影响。

## 2. 权限、Secret 引用与有效策略

- [x] 2.1 为 source allowlist、本地 roots、Secret metadata/assignment、trusted image、Project/Manifest、run 操作和 artifact review 建立 admin/owner/operator/reviewer 服务层授权门禁，并通过跨组织、跨项目和低角色越权测试验证拒绝且不泄露资源详情。
- [x] 2.2 实现只读 Deploy Token Secret reference adapter，使 Importer 可短暂解析凭据且调用方只能读取 metadata/reference，并通过 persistence、日志、审计、错误、runner inspect 和 Workspace 扫描测试验证秘密值不出现。
- [x] 2.3 扩展策略合并为 platform→organization→project→Manifest→Profile→task 的权限交集、保护项并集与数值预算最小值，并通过路径、工具、来源、网络、镜像和各资源预算扩权测试验证低层只能收紧。
- [x] 2.4 统一 source/auth/ref/snapshot/image/mount/scan/runner/resource/cleanup 的稳定失败枚举与脱敏审计 payload，并通过各阶段失败映射测试验证用户响应可行动、内部审计可追踪且均不含秘密。

## 3. 受控 Repository Importer 与 sealed snapshot

- [x] 3.1 实现远程 source 规范化与 pre-connect policy，拒绝 embedded credentials、`file://`、未批准 scheme/host/port 和未经显式批准的内部 HTTP，并通过 URL 变体、端口默认值、大小写/编码绕过测试验证精确 allowlist。
- [x] 3.2 实现逐连接 DNS/IP 与逐跳 redirect 校验，默认阻止 loopback、link-local、metadata、私网和 redirect allowlist 逃逸，仅允许管理员明确批准的内部地址，并通过受控 DNS/redirect 恶意服务测试验证请求不会到达未授权目标。
- [x] 3.3 实现本地只读 root 的 canonical path 与 no-follow snapshot traversal，拒绝任意路径、`..`、symlink 和导入期间路径替换逃逸，并通过 symlink/race/path traversal 测试验证不读取 root 外内容且不 bind 原仓库。
- [x] 3.4 实现受限 Git ref resolve/import，隔离系统/用户 Git config，禁用 hooks、credential persistence、submodule 与 LFS，并执行下载、解包、文件、单文件和 deadline 限额；通过 hooks/submodule/LFS/超限仓库测试验证稳定拒绝与 staging 清理。
- [x] 3.5 将有效导入原子封存为 content-addressed snapshot，净化 remote、hooks、credential helper、alternates 和非冻结引用，同时保留受限 git status/diff/log 所需的本地元数据，并通过相同输入去重、hash 篡改和无远端配置测试验证 snapshot 不可变。
- [x] 3.6 实现 snapshot 引用计数与 staging/孤儿 snapshot 回收，使发布失败不留下可运行部分输入，并通过并发发布、事务失败和清理重试测试验证仍被 Manifest 引用的 snapshot 不被删除。

## 4. Manifest 发布、控制面与 readiness

- [x] 4.1 扩展 Project/Manifest API schema 与现有编辑界面，支持 source type/locator、credential reference、requested ref、可信 image digest 和安全校验状态，确保 secret 仅显示引用/脱敏 metadata，并通过 API 与前端表单测试验证草稿可保存而未授权选项不可选择。
- [x] 4.2 重构 publish 流程为权限→source/secret/image policy→ref resolve/import→源码扫描→snapshot seal→数据库发布事务，并通过每一步故障注入测试验证旧 published version 不被替换且部分快照不可用于 run。
- [x] 4.3 扩展 project readiness 和 run admission，区分 project/Manifest/environment/source/auth/ref/snapshot/image/mount 各稳定原因，并通过列表、详情与创建 run 契约测试验证未就绪项目无法创建 Workspace 或 runner。
- [x] 4.4 冻结 run task contract 和 audit facts，使 branch/tag 后续漂移或 Git 服务离线不改变既有 Manifest 的 commit/snapshot，并通过移动 ref 与断开 Git 服务后的重复 run 测试验证基线一致。

## 5. Snapshot Workspace 与 Compose 路径映射

- [x] 5.1 将 Workspace 准备从每 run 远程 clone 改为仅物化 sealed snapshot，校验 snapshot hash、精确 commit、run identity 和净化 Git 元数据，并通过禁止网络环境下的准备测试验证不访问 Git 服务或 Secret Store。
- [x] 5.2 保持每 run 独立目录并扩展完整性事实，检测 cross-run mount、snapshot/commit 不一致、外部写入和未受控 checkout；通过并发 run、篡改和错误 mount 测试验证 `workspace_integrity_error` 阻止工具与 patch。
- [x] 5.3 实现 Workspace API root→daemon host root 的安全相对路径映射、containment 与 run sentinel 身份校验，禁止缺失映射、猜测路径和跨 run source，并通过路径单元测试验证 `workspace_mount_invalid` fail closed。
- [x] 5.4 在 Docker Compose/部署配置中声明同源 Workspace volume 的 API/host 双根并增加 readiness probe，通过实际部署配置校验验证 API 路径与 daemon host path 指向同一存储。

## 6. 全容器化 Code Tool runtime

- [x] 6.1 定义不可变 RunnerSpec 与 typed RunnerToolRequest/Response 协议，包含 run/container 绑定、helper version、image digest、mount、网络、安全选项、完整资源预算和 bounded output，并通过序列化、非法字段和版本不匹配契约测试验证 fail closed。
- [x] 6.2 在可信 runner image 中实现 read/search/edit/git/status-diff-log/test/shell helper，使用 Workspace-relative fd containment、原子写入、固定命令和输出限制，并通过绝对路径、symlink、shell 拼接、Git 写操作和大输出测试验证不可逃逸。
- [x] 6.3 强化 CodeContainerRunner 按 digest 启动非 root、read-only rootfs、cap-drop ALL、no-new-privileges、默认断网、单一 Workspace rw mount、有界 tmpfs/pids/CPU/memory/time/output/disk，并通过 Docker inspect 契约测试验证实际 facts 与 RunnerSpec 完全一致。
- [x] 6.4 将 CodeToolExecutor 的 read/search/edit/git/test/shell 全部路由到绑定 runner protocol，删除 API Path 与 subprocess 源码操作及任何 host fallback，并通过 monkeypatch 禁止 API 文件/subprocess 访问的工具套件验证六类工具仍可工作。
- [x] 6.5 实施每次工具调用的 active lifecycle、run/container、policy/budget 与 Workspace integrity preflight，runner 不可用或达到资源限制时停止后续动作，并通过 stale container、跨 run、timeout、OOM/pids/disk/output 限制测试验证稳定终态。
- [x] 6.6 保留 ReAct-Engine 的统一任务、事件和审计编排，仅替换 Code Profile executor，并通过 Standard Profile 全量回归与 Code Profile 工具审计测试验证 Standard Agent 不创建 snapshot/Workspace/runner。

## 7. Fail-closed 源码与 patch 验证

- [x] 7.1 实现 Scanner adapter 和不可变 ScanReport，记录 scanner/version、input hash、枚举/扫描/跳过/截断统计、脱敏 findings 与 complete 状态，并通过二进制、大文件、不支持格式、timeout、crash 和 hash mismatch 测试验证不完整结果永不通过。
- [x] 7.2 在 snapshot seal 后、可写 runner 创建前强制校验 source ScanReport，并通过 source secret hit 与 scanner failure 测试验证不启动容器、不向模型或响应暴露命中内容。
- [x] 7.3 在 Workspace 冻结后对 canonical diff 和全部新增内容强制执行 patch scan，再交给 Verifier/sealer，并通过 secret hit、untracked binary、scan truncation 与 scan-report input drift 测试验证不得产生 `patch_ready`。
- [x] 7.4 将 source/patch ScanReport hash、Verifier facts、snapshot/commit、image digest 和 policy hash 关联到 sealed artifact，并通过 artifact 完整性与部分封存失败测试验证任何缺失均以非成功终态结束。

## 8. 容器与 Workspace 持久化生命周期

- [x] 8.1 将 run、runner、Workspace 的执行资格与 cleanup 状态持久化，终态先原子撤销 active route，再停止并立即删除容器/临时网络，并通过 success/cancel/timeout/policy/infrastructure 各终态测试验证无新工具调用可进入。
- [x] 8.2 将 Workspace 默认保留改为 168 小时的不可执行 `retained_read_only` 状态，支持平台/项目更短期限或立即删除，且不与 artifact/audit retention 联动；通过 retention 计算、权限和不可下载/不可重新挂载测试验证边界。
- [x] 8.3 实现幂等 lifecycle janitor、启动恢复、重试计数、next-attempt 与 `sandbox_cleanup_failed`/Workspace cleanup 告警，并通过模拟 API 崩溃、Docker 删除失败和文件删除失败测试验证最终收敛且不误删其他 run。
- [x] 8.4 实现 snapshot 与 Workspace 的容量指标、低水位 admission/kill switch 和安全回收顺序，并通过磁盘限额与仍被引用资源测试验证不会为释放空间破坏已发布 Manifest 或长期工件。

## 9. 集成、安全回归与发布门禁

- [x] 9.1 建立真实 Docker bind-path 集成套件，从 API root 写 sentinel、runner 读取/修改、API 再验证，并断言错误 host root、空目录、跨 run path、额外 mount、Docker socket 和 digest mismatch 均阻止启动。
- [x] 9.2 建立 Repository Importer 安全矩阵，覆盖 HTTPS/SSH/批准内部 HTTP、私有 Deploy Token、无效认证、DNS/IP/redirect SSRF、local-root escape、hooks/submodule/LFS 和各资源限制，并验证网络请求、临时目录、日志与审计无秘密泄露。
- [x] 9.3 建立从 publish→snapshot→source scan→Workspace→六类容器工具→Verifier/patch scan→artifact→终态 cleanup 的端到端测试，并验证 branch 漂移与运行期 Git 服务离线时仍使用相同 commit 且原仓库保持不变。
- [x] 9.4 执行数据库迁移、API/控制面、权限、策略、Workspace、runner、tools、Verifier、artifact、lifecycle、前端和 Standard Agent 回归测试，并保存所有命令与通过结果作为本 change 的验证证据。
- [x] 9.5 在具备 Docker daemon 的发布环境验证 Compose readiness、runner inspect 和 cleanup recovery，随后运行 `openspec validate code-agent-secure-workspace-runtime --strict`；仅当代码、测试、部署门禁与 OpenSpec 严格校验全部通过后才完成并勾选本任务。
