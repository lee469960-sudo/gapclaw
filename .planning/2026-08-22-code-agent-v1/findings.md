# Findings & Decisions: code-agent-v1

## Requirements Source of Truth

- 正式实现任务：`openspec/changes/code-agent-v1/tasks.md`。
- 行为验收：`openspec/changes/code-agent-v1/specs/` 的六份 capability spec。
- 架构与边界：`openspec/changes/code-agent-v1/design.md`。
- 本计划不重新定义上述需求；后续仅记录事实、影响与待决事项。

## Confirmed Architecture Constraints

- 统一 `run_agent → AgentContext → AgentRuntime.run` 与单一 ReAct 主循环继续存在；CodeAgent 通过显式 `profile=code` 接入。
- 未声明 Profile 的 Agent 默认为 `standard`；Standard Agent 的工具路由、事件、终态及可用性必须零行为回归。
- V1 仅针对 allowlist 内部、非生产、受信任仓库；默认断网，无真实秘密、自动 Git 写入、动态工具或自动委派。
- 每个 Code run 使用固定 base commit 的独立 Workspace；只有 Verifier 通过且 canonical diff/报告完整封存时才可产生 `patch_ready`。
- V1 runner 是受限容器边界，不被视为不可信第三方代码的 microVM 级隔离。

## Execution Findings

- 2026-08-23 归档预检确认 31/31 tasks 与全部 artifacts 均完成；用户选择同步后归档。六份 delta specs 已逐项合并并验证，change 已移动到 `openspec/changes/archive/2026-08-23-code-agent-v1/`，无不完整 artifact/task warning。

- 2026-08-23 运行时回归：已有 SQLite `code_projects` 表缺少 `visibility` 列，Agent 编辑页的 `action=refs` 查询该表时稳定报 `sqlite3.OperationalError: no such column: code_projects.visibility`。`init_db` 目前只迁移 `environment_tier`，未迁移 `visibility` 和 `allowed_users`；这违反 task 1.2 的既有数据库持久化兼容性，故重新打开该任务，范围仅为补齐这两个列迁移及其回归测试。

- Runtime 入口与主循环：`apps/api/app/services/agent_runtime/runtime.py` 和 `context.py`；工具分发：`apps/api/app/services/agent_tools.py`。
- Agent 持久化和 API/schema 现有落点：`apps/api/app/models.py`、`schemas.py`、`routers/agent.py`；前端路由：`apps/web/src/router.js`。具体落点待 task 1.1 审查确认。
- task 1.1 的持久化兼容迁移由 `apps/api/app/startup.py:init_db` 的列存在性检查管理，而非 Alembic；Agent 请求 schema 是 `routers/agent.py` 的 `AgentBody`。Profile 兼容改动应保持在模型、该请求模型、该路由和启动迁移中，避免引入第二套 schema/迁移机制。
- task 1.2 审查未发现既有 Code 项目、Manifest 或任务契约模型。已新增独立 `code_projects`、`code_project_manifests` 与 `code_agent_runs` 控制面表，以及 `services/code_agent/control_plane.py`；它们不修改 Standard `run_agent` 路径。只有已发布、字段完整的 Manifest 才能创建 `pending` Code run，且 Manifest 后续变动不会修改已冻结的 JSON 快照。
- task 1.3 采用显式的 `platform → organization → project → profile → task` 输入顺序。平台基线固定断网、允许工具与预算上限；后续层只可将路径细化、工具取子集或预算降低，扩张均通过稳定 `policy_*` 原因拒绝。
- task 1.4 使用现有资源 ACL（管理员、创建者、`allowed_users`）授权 Code 项目。Agent 保存 `code` Profile 时需明确绑定可访问项目；`submit_chat` 的 Code 分支只会在契约/Manifest 验证成功后创建 `pending` run，尚未接入 Runtime 前不排入 Standard 后台任务。
- task 2.1 的 Runtime 编译入口是 `run_agent`。`CodeExecutionContext` 只保存 run 已冻结的标识与 JSON 快照；Code Profile 解析时清除 Agent 的既有 MCP、Skill、RAG、HTTP MCP 与通用 action 授权，避免配置复用成为能力提升。
- task 2.2 的已有事件总线是 `agent_runtime.hub`；Code 生命周期事件使用现有 Agent/session envelope 并添加 `profile` 版本化 payload，避免修改既有 `step`/`done` 消费者的字段约定。
- task 2.3 复用既有 `_running` 取消门禁，并在每个工具动作前补充二次检查。Code 专属的超时、run 状态与清理由 `run_agent` 外层包装；Workspace/runner 后续通过 `register_code_cleanup` 接入，无需改动 ReAct 主循环。
- task 2.4 的兼容性覆盖直接验证未持久化 `profile` 默认 standard、原 `allowed_actions` 保留、Standard 不产生 Profile 事件，并复用既有 single-loop 测试确认工具路由和 FINAL 终态。
- task 3.1 不复用 Standard `workplace.py`。Code Workspace 使用 run 独立目录，先创建无工作树裸镜像，再用固定 commit 的 `git archive` 安全展开；该路径不会运行 checkout hook、submodule 或仓库工作树代码，并把 resolved commit 等源码事实持久化到 `CodeAgentRun`。
- task 3.2 使用既有 Docker client 但新增专用 `CodeContainerRunner`：固定 `network_mode=none`、只读根文件系统、capabilities 全丢弃、no-new-privileges、资源上限和唯一 Workspace 挂载；所有额外挂载、特权、嵌套容器或网络请求在 Docker 调用前拒绝。
- task 3.3 将 baseline 文件清单与授权写入账本保存在 Workspace 同级 `control/`，该目录不挂载进 runner。写入授权前和封存前比较当前快照；基线事实漂移或不在账本内的变更将 run 标记为 `workspace_integrity_error`。
- task 3.4 的清理顺序由 lifecycle 回调 LIFO 保证 runner 容器先删除、Workspace 后冻结。源码镜像立即删除，工作树去除写权限并保留 24 小时诊断窗口；未封存状态始终不可下载，到期后清除整个 run 目录。
- task 4.1 通过 `AgentContext.tool_executor` 将 Code 工具注入统一循环，Standard 仍回落到原 `execute_action`。Code read/search/edit/test 使用独立 native schema；test 仅引用冻结验证计划索引，所有调用计入 run 预算和审计。
- task 4.2 的 restricted shell 只接受有效策略中的完整命令且拒绝任意 shell 元字符；平台层只授权可执行文件，项目层可冻结完整命令。Git 使用受管裸镜像、独立 index 和固定 status/diff/log 参数，不暴露 commit/push/PR 或凭据操作。
- task 4.3 将 run/Workspace 状态、冻结能力、预算、路径和 action 参数统一在工具副作用前预检。拒绝结果仅返回稳定 reason 与平台文案，越权路径不会进入反馈或审计；仓库文本和命令输出不参与有效策略计算，不能提升运行中权限。
- task 4.4 在 Code 工具结果进入 Runtime archive、step 事件和模型上下文前统一完成秘密样式分类与脱敏，审计只记录分类/计数而不保存原文。Code 工件访问不可拥有独立的放宽 ACL，必须同时匹配项目且通过 Code Project ACL。
- task 5.1 将 Verifier 放在共享 ReAct loop 返回之后、runner/Workspace 清理之前；它不读取模型完成声明，而是重新检查 Workspace、受保护/测试路径与秘密样式，并逐项执行冻结 validation plan。验证报告持久化在 Code run，失败通过统一 Profile verify 事件和稳定结果阻断成功。
- task 5.2 在模型执行前用同一 runner 执行冻结 validation plan 并持久化脱敏基线，随后从固定 commit 恢复 Workspace，避免测试副作用成为 patch。最终 Verifier 对同一索引比较 exit code，分类新增失败、既有失败和基线/环境不可用；后两类均保持显式非成功结果。
- task 5.3 在 Verifier 通过后先停止 runner，再从受管 mirror 的 base tree 与 Workspace 生成规范化 `git diff --no-index`。patch、策略、Verifier 报告和 manifest 在同一临时目录完成哈希后原子改名；数据库提交失败会恢复只读目录权限并回滚整套新工件，不留下部分 `patch_ready`。
- task 5.4 以独立结果序列化层统一稳定终态、标签和可采用性；只有 `patch_ready`、Verifier passed 与 sealed artifact 同时成立才是直接可采用。Code run 绑定 session，项目 ACL API 返回最新结果；AgentChat 对所有非可采用终态显示醒目警告，模型消息本身不决定 UI 成功状态。
- task 5.5 的审阅/下载只允许固定的 sealed artifact 文件并在每次访问时核验 manifest 与内容哈希。接受动作检查最新 published Manifest 的 base commit，过期则转为 `stale`；成功仅新增带 manifest hash 的人工审阅记录，不执行 commit/push/PR 或任何 Git 写操作。
- task 6.1 使用独立持久化 kill switch 控制面，并只在 Code run 创建前按 global→project→repository→tool→image→model 检查。命中返回稳定 scope reason；不会修改既有 Code run，Standard Agent 提交路径不读取这些开关。
- task 6.2 将 Code 后台任务送入专用 daemon worker queue，Standard 继续既有 BackgroundTasks 路径。项目并发在 run admission 计数，变更文件与 diff 行数分别在 Verifier/Sealer 强制；迭代、工具调用、变更规模、diff 与耗时写入 `budget_usage`，超限均显式 `budget_exhausted` 且不降低验证/封存要求。
- task 6.3 增加可独立运行的攻击者视角负向矩阵，将既有离散安全断言串成端到端边界验证：仓库指令仅作数据、越权和 shell/network 逃逸在副作用前拒绝、秘密在反馈前脱敏、保护测试/污染阻断 Verifier、取消后 Workspace 只读且不可下载。
- task 6.4 以计划、逐 run 观测和不可变汇总报告三层建立试点工件，不伪造真实观察结果。计划硬性要求 3 个启用的 internal_non_production 项目、10 个覆盖三项目的 low-risk 任务；观测从 run/artifact/review 派生复现性、接受、耗时与清理，并接收账单成本微单位，汇总 JSON 原子写入且带 SHA-256。
- task 6.5 将 6 份 delta spec 的 28 个 Scenario 映射到对应 Code/Standard 可执行测试。完整 `apps/api/tests`、Vite production build、Runtime 专项、OpenSpec strict validate 和 diff whitespace 门禁均通过；根级 pytest 仅因误收集需要 live API 的 `scripts/smoke_test.py` 在沙箱导入期失败，正式 tests 目录无失败。
- OpenSpec `code-agent-v1` 已通过 `openspec validate code-agent-v1 --strict`；尚未执行实现任务或测试。

## Technical Decisions

| Decision | Rationale |
|---|---|
| Profile 在 Runtime 入口编译为统一执行上下文 | 避免在 ReAct 主循环中形成 CodeAgent 专用分支或第二运行时。 |
| Manifest/策略与任务契约在 run 创建时冻结 | 保障审计、可复现与最小权限，禁止运行中权限提升。 |
| 代码工具采用类型化适配器，shell 只是受限兜底 | 允许确定性路径、命令、预算与输出治理。 |
| Verifier 和工件封存独立于模型 FINAL | 系统事实而不是模型叙述决定是否可交付。 |

## Verification Findings: 2026-08-23

- **CRITICAL — 大文件可绕过秘密检测。** `verifier.py:169` 只扫描不超过 2,000,000 bytes 的变更文件，且 edit/diff 预算没有等价字节上限。隔离探针写入 2,000,037 bytes 且包含秘密样式内容，Verifier 返回 `verification_passed`。修复时必须对所有可封存内容执行流式/分块扫描，或对无法完整扫描的文件 fail closed，并增加大文件与二进制负向测试。
- **CRITICAL — V1 环境 tier 未在 run admission 执行。** `CodeProject.environment_tier` 只在 pilot evaluation 中检查；`control_plane.py:_published_manifest` 仅检查项目存在和 enabled。内存数据库探针表明 `production` 项目可创建 `pending` Code run。修复时应在 `create_code_run` 前拒绝非 `internal_non_production` 项目，并验证仓库 allowlist/环境 tier 的稳定拒绝原因。
- **CRITICAL — runner 命令不受冻结超时约束。** `runner.py:110` 接收 `timeout_seconds`，但同步 `container.exec_run` 未使用或执行外部取消；探针传入 0 秒仍等待命令完成。Runtime 的 `asyncio.wait_for` 仅包住 ReAct loop，且同步 Docker 调用会阻塞事件循环。修复时应实现可强制终止的容器 exec deadline，并覆盖 tool、baseline、final verifier 三条路径。
- **CRITICAL — prepare/start 异常绕过统一清理与终态。** Workspace prepare、cleanup hook 注册和 runner start 位于 `_run_code_runtime` 的终结保护之外；runner start 失败探针留下 `pending` run、未调用 retention cleanup、Workspace 仍可写。修复时应从第一项资源分配开始使用统一 try/finally/补偿清理，并把失败持久化为 `infrastructure_error`。
- 现有 28 个 Scenario 均有命名测试映射，但“工具执行中超时”测试只模拟可取消的异步 `sleep`，未覆盖真实同步容器 exec；因此测试映射完整不等于该场景行为完整。
- 本轮完整后端测试 349 项、前端 production build 与 OpenSpec strict validate 均通过，说明上述问题属于现有测试未覆盖的需求/设计缺口，而非当前回归失败。
- 2026-08-23 重新完整读取当前 `proposal.md`、`design.md`、六份 delta spec 与 `tasks.md`；OpenSpec 正式任务仍为原 26 项且全部勾选，没有纳入上述 4 个 CRITICAL 的修复任务。由于 `tasks.md` 是唯一正式任务来源，Planning with Files 只保留这些问题为 findings，不把它们伪装成新的 execution task，也不启动修复。
- 用户确认后已更新 OpenSpec：`design.md` 明确 admission、runner deadline、首资源补偿清理和全内容秘密扫描的 fail-closed 决策；四份既有 capability spec 新增 5 个可测试 Scenario；`tasks.md` 新增正式 Phase 7（7.1–7.5）。proposal 的目标与边界已覆盖这些修复，保持不变。
- task 7.2 采用 runner 专属 `RunnerCommandTimeout` 与每容器绝对 monotonic deadline：阻塞的 Docker exec 在可等待线程边界运行，剩余时限耗尽即 kill 容器并抛出专属超时。该异常只在 Code Tool 分支穿透共享 ReAct 的通用工具异常处理，baseline 与 final Verifier 也不降级吞掉它，最终统一映射为 `timed_out` 并执行既有 cleanup；Standard 工具异常语义保持不变。
- task 7.3 的补偿注册点位于实际资源分配边界而非完整启动之后：`WorkspaceManager` 在创建 run 根目录时记录所有权，`run_agent` 在调用 prepare 前注册可识别部分分配的 cleanup；runner 在 Docker 返回容器后立即注册 remove。启动外层只负责统一持久化 `infrastructure_error`、执行所有已登记回调和显式升级 `cleanup_failed`，从而保留单一 lifecycle 且不让直接使用 WorkspaceManager 的非 Runtime 调用污染全局 cleanup registry。
- task 7.4 将“完整扫描证明”定义为最终 changed-path 集合（含删除和 symlink）及其逐内容 SHA-256 的规范化组合指纹。Verifier 以 1 MiB 块和 4 KiB 跨块窗口扫描，最终验证命令之后重新校验完整性并保存 `secret_scan_complete/content_hash`；sealer 冻结 runner 后重新求 changed paths、完整扫描并比对指纹。特殊文件、短读/竞态、I/O 失败、秘密命中或报告缺少证明均 fail closed，因此旧式单一 `passed=true` 不再足以封存。
- task 7.5 的首次 Coherence 审计发现 7.3 在 runner 成功后到 `_run_code_runtime` 接管前仍有 Context/依赖构建清理空窗；已临时撤回 7.3，并将公开 `run_agent` 提升为包住完整内部实现的 pending-Code 补偿守卫。新增 runner 已启动后 Context 构建失败测试后，最终 17/17 Requirements、32/32 Scenarios 与 8/8 design decisions 均无剩余 CRITICAL/WARNING。

## Issues Encountered

| Issue | Resolution |
|---|---|
| 全局 catch-up 脚本路径不存在 | 改用仓库本地脚本；成功执行，无待恢复上下文。 |
| 初始化内容替换的首个补丁格式无效 | 删除模板并用完整文件补丁重建；未写入错误内容。 |

## Resources

- `openspec/changes/archive/2026-08-23-code-agent-v1/proposal.md`
- `openspec/changes/archive/2026-08-23-code-agent-v1/design.md`
- `openspec/changes/archive/2026-08-23-code-agent-v1/specs/`
- `openspec/changes/archive/2026-08-23-code-agent-v1/tasks.md`
- `docs/react-engine.md`
