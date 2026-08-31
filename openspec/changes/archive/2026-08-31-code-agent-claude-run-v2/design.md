## Context

现有 CodeAgent 已具备 Task、Manifest、Repository、Workspace、Sandbox、Skill/MCP、运行事件、Verifier 和结果管理。该变更只增加一种受控的 local 发布动作，必须复用现有 run 生命周期和安全边界；动机与范围见 proposal.md，行为契约见 specs/。

## Goals / Non-Goals

**Goals:**

- 在现有 CodeAgent runner 内执行一个 Manifest 登记的 canonical local 发布命令。
- 在 Run 创建时冻结命令、local target、镜像 digest、网络、Secret、预算和验证计划。
- 通过 preflight/dry-run、人工确认、单次执行、证据收集和独立 Verifier 形成闭环。
- 让普通 CodeAgent run 保持原有行为，并让发布过程可审计、可观察、可回滚到旧能力。

**Non-Goals:**

- 不创建新的 Sandbox、Repository 管理器或通用任意 CI 执行器。
- 不支持 dev/pre/prod 发布，不自动执行 Git 提交，不提供自动补偿重试。
- 不在运行时联网安装工具，不允许模型绕过 Manifest、策略或 Verifier。

## Decisions

### 1. 通过显式发布命令注册表而不是任意 Shell

Manifest 只引用 `local_publish_command_id`。平台维护命令 ID 到仓库脚本/入口、固定参数、依赖和验证计划的映射；Claude Code 只能请求该 ID。这样可审计、可禁用并避免模型拼接危险命令。相比解析任意 CI YAML，显式注册避免语义歧义和隐式步骤。

### 2. 复用现有 Run/Sandbox，而不是新建发布执行平面

发布动作作为现有 CodeAgent run 的受控阶段运行，沿用 Workspace bind、非 root、只读 rootfs、capability drop、资源预算和清理流程。发布能力只在冻结策略声明的网络例外下启用；普通 CodeAgent 仍保持默认隔离。

### 3. 固定双架构镜像只承载平台基础工具

为发布能力构建 `amd64`/`arm64` 镜像，预装 CodeAgent/Claude Code 运行和受控命令执行所需的固定基础工具，并以 digest 固定。dbt、jq、clickhouse-client 等项目/CI 业务工具不是 runner 启动必备项；平台可对 Manifest 登记的依赖做可选探测和审计，但缺失只能在具体发布命令阶段形成清晰失败，不能阻塞 Workspace/Runtime 启动。运行时不执行 apt/pip/npm 安装；若项目需要额外工具，应由项目 CI 脚本、项目专用镜像或后续 Manifest 版本显式承载。

### 4. 凭证通过 Secret 引用短暂注入

Manifest 保存 Secret 标识而非值。preflight 校验引用可用性，正式命令启动时以环境变量或等效临时通道注入，日志和 transcript 统一脱敏，Run 结束立即清理。Git、镜像和发布 API 凭证分离，避免复用高权限身份。

### 5. 两阶段执行与不可重试副作用

阶段一执行 preflight/dry-run 并等待一次性人工确认；阶段二执行 canonical 命令一次。网络中断或超时不自动重试，转为状态未知并保留发布 ID/恢复线索。发布锁按仓库+环境维度防止并发。

### 6. Verifier 以三类证据裁决

发布进程退出码、远端发布证据（如任务 ID）和 local 目标状态验证必须同时满足。Claude Code 最终文本仅作为摘要，不参与成功裁决。证据缺失、Git 源文件发生非允许修改或验证失败均阻止成功。

### 7. 通过统一事件流展示过程

新增 `publish_preflight`、`publish_confirmation_required`、`publish_started`、`publish_output`、`publish_evidence`、`publish_verified`、`publish_unknown` 等脱敏事件，复用现有对话和历史回放。用户看到命令 ID、阶段、退出码和摘要，不看到秘密或完整敏感参数。

## Risks / Trade-offs

- **[Risk] canonical 命令内部仍调用未登记的脚本或服务** → 发布注册表固定入口、参数和网络目的地，preflight 检查依赖与路径，Verifier 检查最终证据。
- **[Risk] 发布超时但远端已经生效** → 不自动重试；保留远端任务 ID和状态查询方式，进入状态未知并要求人工确认。
- **[Risk] 项目业务依赖缺失或版本漂移** → 基础 runner 不把 dbt/jq/clickhouse-client 作为通用必备；依赖探测只写审计事实，具体命令缺失时以发布阶段失败和恢复提示暴露。
- **[Risk] 凭证或命令输出泄露秘密** → Secret 分离注入，统一脱敏，禁止命令参数携带秘密，并对 transcript 做二次扫描。
- **[Risk] 发布动作影响现有 CodeAgent 稳定性** → 能力由 feature flag/Manifest 显式启用；禁用发布能力即可回退到原有 CodeAgent 路径。

## Migration Plan

1. 增加命令注册、Manifest 字段和策略校验，默认关闭 local publish。
2. 构建并批准双架构发布镜像，登记 digest、可选依赖探测结果、网络目的地和验证计划。
3. 为一个试点仓库发布仅含 local 命令的 Manifest，先执行 preflight/dry-run，再开启人工确认后的正式发布。
4. 观察审计、证据和状态未知场景；出现问题时撤销 feature flag/Manifest 或镜像 digest，不影响普通 CodeAgent。

## Open Questions

无。canonical 命令的具体路径、参数和验证端点属于实施前对目标仓库/CI 的事实核对，不改变本设计的架构和行为契约。
