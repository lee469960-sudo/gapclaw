# Progress Log: code-agent-v1

## Session: 2026-08-22

### Current Status

- **Phase:** Archived
- **OpenSpec tasks completed:** 31/31
- **Current formal task:** none
- **Implementation status:** 实现、verification remediation、主 specs 同步与 archive 均已完成。

### Task 1.2 Regression Repair: 2026-08-23

- `startup.init_db` 现在对既有 `code_projects` 表补充 `visibility VARCHAR(16) DEFAULT 'private'` 和 `allowed_users TEXT DEFAULT '[]'`，保留已有 `environment_tier` 迁移方式。
- 新增 `tests/test_startup_migrations.py`：从缺少这两个列的旧 SQLite 表启动，验证迁移完成后 Agent 编辑页的 `action=refs` 能成功返回 Code Project 引用。
- 验证：`pytest tests/test_startup_migrations.py tests/test_code_agent_control_plane.py -q` → 24 passed；`pytest tests -q` → 370 passed；`git diff --check` → clean。
- 本地 API 进程的 8000 端口仍被 PID 17366 占用但拒绝连接，未自动重启以避免中断用户的现有开发服务；下次正常启动会执行该兼容迁移。

### Actions Taken

- 2026-08-23 用户选择同步后归档：`agent-config` 与 `agent-runtime` 各追加 CodeAgent Requirements，并创建 `code-agent-profile`、`code-agent-project-policy`、`code-agent-verification`、`code-agent-workspace` 四份主规范。
- 同步后逐 capability 比较一致；`openspec validate --specs` 为 10 passed/0 failed，主 specs 无 delta 操作标记，`git diff --check` clean。
- change 已移动到 `openspec/changes/archive/2026-08-23-code-agent-v1/`；归档内 31/31 tasks 完整，active change 列表为空，无归档 warning。
- 2026-08-23 执行 archive preflight：artifact 状态全部完成、OpenSpec tasks 31/31；主规格同步评估发现六份 delta specs 均有待合并内容。已暂停在用户选择门禁，本轮未写主规格、未移动 change、未修改 `tasks.md`。
- 完整读取 `code-agent-v1` 的 `proposal.md`、`design.md`、所有 `specs/*/spec.md` 和 `tasks.md`。
- 初始化 `.planning/2026-08-22-code-agent-v1/`，并将其设为 active plan。
- 将 `tasks.md` 的 26 项正式任务映射为六个 execution phases；未修改 `tasks.md`，未勾选任何任务。
- 读取当前 Runtime、模型/schema/API 的高层文件落点，作为 task 1.1 的后续调查起点。
- task 1.1：已在 Agent 模型、序列化、请求 schema、保存 API 和启动兼容迁移中实现可选 `profile`；未携带字段的新建请求显式保存为 `standard`，更新请求保留既有 Profile。已新增定向回归测试，等待执行验证后再勾选 OpenSpec 任务。
- task 1.2：已新增版本化项目 Manifest、独立 Code run 持久化与冻结契约服务；缺失、草稿或字段无效的 Manifest 在写入 run 前失败，等待定向测试验证后再勾选 OpenSpec 任务。
- task 1.3：已将 Manifest 冻结路径、工具和预算纳入平台→组织→项目→Profile→任务的单向收紧合并，并实现稳定拒绝原因；等待定向测试验证后再勾选 OpenSpec 任务。
- task 1.4：已实现 Code Profile 的项目绑定与 ACL 校验，并将 Code 聊天提交接入 Manifest/任务契约门禁；未授权用户和缺失 Manifest 的提交会在 run 创建前被拒绝，等待定向测试验证后再勾选 OpenSpec 任务。
- task 2.1：已在 `run_agent` 解析 Profile 并构建不可变 `CodeExecutionContext`；显式 Code run 仅接受匹配的 pending 契约，且不会继承 Standard 工具绑定。等待定向测试验证后再勾选 OpenSpec 任务。
- task 2.2：已在统一 Runtime 中为 Code run 发布 prepare 和 terminate 的版本化 Profile payload，供后续 verifier/sealer 生命周期复用；等待定向测试验证后再勾选 OpenSpec 任务。
- task 2.3：已实现 Code run 绑定、取消原因、冻结超时、状态持久化和可扩展清理回调；工具批次会在每次动作前检查停止状态，清理失败会写入 `infrastructure_error/cleanup_failed` 并发布事件。等待定向测试验证后再勾选 OpenSpec 任务。
- task 2.4：已新增 Standard Profile 专项兼容测试，覆盖缺失 Profile 默认执行、原工具授权、统一 done 事件字段与无 Code payload；等待与既有单循环/API 回归一起验证后再勾选 OpenSpec 任务。
- task 3.1：已新增独立 Code Workspace manager，并在 Code Context 构建前按冻结 commit 准备源码、持久化 Workspace 路径和源码事实；新增真实 Git 仓库的固定基线、禁用 hook 与并发隔离测试，等待验证后再勾选。
- task 3.2：已新增受限容器 runner，固定断网、挂载、capability、rootfs 与资源参数，持久化镜像/容器事实并注册强制清理；等待 mock Docker 的正向/负向测试和 Runtime 回归后再勾选。
- task 3.3：已新增 Workspace 基线哈希、不可见 control 元数据、写入前授权与封存前完整性校验；基线漂移及未解释外部写入会设置稳定错误终态，等待测试后再勾选。
- task 3.4：已把 Workspace 清理注册到统一 lifecycle，终止时删除源码镜像、冻结工作树为只读且禁止下载，并支持默认 24 小时后安全清除；等待终止/留存测试后再勾选。
- task 4.1：已实现类型化 Code read/search/edit/test、原生工具 schema 和 Context 注入；适配器执行路径/Workspace 完整性、工具预算与审计约束，test 仅执行冻结验证计划。等待定向及 Standard loop 回归后再勾选。
- task 4.2：已实现完整命令 allowlist 的 `code_shell` 与仅 status/diff/log 的 `code_git`，并加入 native schema；命令逃逸、任意网络命令和 Git 写操作在执行前拒绝。定向负向测试与 Runtime 回归已通过，可以勾选。
- task 4.3：已将冻结能力、预算、Workspace/Runner 状态、路径和工具参数集中在副作用前预检；拒绝反馈和审计不回显越权输入，仓库文本无法改变冻结策略。定向安全测试和统一循环回归已通过，可以勾选。
- task 4.4：已在 Code 工具唯一输出边界实现秘密样式分类/脱敏，确保 Runtime 事件、日志和模型反馈不接触原始秘密；工件授权强制继承且不能放宽项目 ACL。定向安全与 Runtime 回归已通过，可以勾选。
- task 5.1：已在模型循环后、清理前接入系统强制 Verifier，持久化事实报告并重新执行冻结测试计划；受保护路径、测试完整性和秘密检测失败均阻断成功，模型 FINAL 无法绕过。定向与 Runtime 测试已通过，可以勾选。
- task 5.2：已在模型修改前记录脱敏验证基线并恢复固定源码，最终 Verifier 区分新增失败、既有失败与环境不可用；缺失/不确定基线保持非成功。定向分类与 Runtime 回归已通过，可以勾选。
- task 5.3：已在 Verifier 通过后冻结 runner，从固定基线与 Workspace 生成 canonical diff，并原子封存 patch、策略、镜像和 Verifier 报告哈希；缺失/提交失败不会留下部分工件。定向封存与 Runtime 回归已通过，可以勾选。
- task 5.4：已实现稳定结果序列化、Code run session 查询与 AgentChat 状态横幅；只有完整验证且已封存的 `patch_ready` 可直接采用，其余结果均显著标记不可采用。后端定向测试和 Vite production build 已通过，可以勾选。
- task 5.5：已实现项目 ACL 下的 patch/Verifier/哈希审阅与固定文件下载；接受动作复核封存哈希和最新基线，只写不可变审阅引用且不修改 Git。定向安全测试和 Vite production build 已通过，可以勾选。
- task 6.1：已实现全局、项目、仓库、工具、镜像、模型粒度的持久化 kill switch，并在 Code run 创建前拒绝命中项；既有 Code run 与 Standard Agent 不受影响。定向控制面与 Standard 回归已通过，可以勾选。
- task 6.2：已实现 Code 专用后台队列、项目并发 admission、变更文件/diff 资源配额和迭代/工具/规模/耗时观测；超限显式进入 `budget_exhausted` 且不产生部分工件。定向测试已通过，可以勾选。
- task 6.3：已建立集中安全负向测试，覆盖提示注入、路径越权、秘密回显、网络/命令逃逸、保护测试、取消清理与 Workspace 污染，并与相关组件回归一起通过，可以勾选。
- task 6.4：已建立严格三项目/十低风险任务的试点计划、实际 run 指标采集与不可变汇总报告，覆盖验证复现性、人工接受、成本、耗时和清理结果。定向测试已通过，可以勾选。
- task 6.5：已建立 6 个 capability/28 个 Scenario 的测试追溯矩阵，并完成全量后端、前端生产构建、Runtime 兼容专项、OpenSpec strict 与 diff 检查；全部正式门禁通过，可以勾选。
- task 6.5 已按约定在验证与进度记录完成后勾选；OpenSpec apply 状态确认为 `all_done`（26/26），无未勾选任务。
- 2026-08-23 按用户要求重新读取当前 OpenSpec change 并恢复 Planning with Files：确认 `.planning/.active_plan` 指向本计划，六个 execution phases 仍逐项映射 `tasks.md` 的 1.1–6.5，未重新定义需求或新增正式任务；当前任务状态仍为 26/26。
- 2026-08-23 经用户逐工件确认后更新 `design`、四份相关 specs 与 `tasks.md`：新增 Phase 7 的 7.1–7.5，Planning with Files 已按唯一正式任务源映射为第七 execution phase；本次仅修改规划工件，未开始 implementation。
- task 7.1：已在 Manifest 查询和任何 run/Workspace 创建前强制项目 `environment_tier == internal_non_production`；production、staging 和空 tier 均返回稳定 `project_environment_not_allowed`，定向控制面与 Standard 兼容测试通过，可以勾选。
- task 7.2：已为每个受管容器保存冻结的 monotonic deadline，并把 Docker exec 放入可等待的线程边界；命令超过请求时限或 run 剩余时限时立即 kill 容器并抛出专属超时。Code Tool、baseline 和 final Verifier 均让该异常穿透至统一 Runtime，run 进入 `timed_out`、阻止后续工具动作并执行现有 cleanup。runner/tool/verifier/runtime/Standard 定向测试 40 项、Python 编译与相关 diff whitespace 检查均通过，可以勾选。
- task 7.3：已在 Workspace run 根目录创建和 Docker 容器返回后的第一时点登记补偿所有权/删除回调，并用 Code 启动外层统一捕获 prepare/start 失败；成功补偿后 run 固定为 `infrastructure_error/startup_failed`，补偿失败则显式保存 `infrastructure_error/cleanup_failed`。部分 Workspace 被删除或冻结只读，失败 run 不能以 pending 重试；49 项组合回归、Python 编译与相关 diff whitespace 检查通过，可以勾选。
- task 7.4：已对最终 changed paths 的全部文本、大文件、二进制、删除和 symlink 内容执行 1 MiB 分块/4 KiB 跨块的有界扫描与规范指纹；特殊文件、短读/竞态、I/O 失败均 verification 非成功。Verifier 在验证命令后保存完整扫描证明，sealer 冻结 runner 后重新扫描并比对 changed paths/指纹，旧 `passed=true` 或内容漂移不能封存。CodeAgent 专项 98 项、Python 编译与相关 diff whitespace 检查通过，可以勾选。
- 7.5 change verification 发现并重新打开 7.3：prepare/start 异常已有补偿，但 runner 成功后到 `_run_code_runtime` 接管前的 Context/依赖构建异常仍可能绕过清理。全量门禁虽通过，该场景缺少实现与测试，按 design 视为必须修复的 CRITICAL；完成外层 lifecycle guard 与新回归前不计入 7.3/7.5 完成。
- 7.3 verification 修复：公开 `run_agent` 已成为 Code 启动的最外层补偿守卫；仅对已验证的 pending Code run 生效，包住完整 `_run_agent_impl`，Context/依赖构建失败仍持久化 `infrastructure_error` 并清理。新增 runner 已启动后 Context 构建失败回归，与 runner/workspace/Runtime/Standard/single-loop 共 32 项通过；该 CRITICAL 已清零，可以重新勾选 7.3。
- task 7.5：已完成四项修复的安全负向覆盖，最终完整后端 369 项、Runtime/Standard 专项 20 项、前端 production build、OpenSpec strict validate 与全仓 diff check 均通过。change verification 对 31 tasks、17 Requirements、32 Scenarios 和 8 项 design decisions 完成 Completeness/Correctness/Coherence 审计；审计中发现的 runner 后 Context 构建清理空窗已修复，最终无剩余 CRITICAL/WARNING，可以勾选。
- task 7.5 已在上述进度、代码与测试确认后勾选；`openspec instructions apply --change code-agent-v1 --json` 返回 31/31、`state=all_done`，勾选后的 strict validate 与全仓 diff check 仍通过。本轮未执行 archive。

### Test Results

| Test | Expected | Actual | Status |
|---|---|---|---|
| `openspec validate code-agent-v1 --strict` | 变更工件有效 | 已在 OpenSpec 规划完成时通过 | ✅ |
| `python -m pytest tests/test_code_agent_profile.py tests/test_react_engine_v4.py -q` | Profile 默认值/API 兼容与既有 Agent 配置回归 | 18 passed | ✅ |
| `python -m pytest tests/test_code_agent_control_plane.py tests/test_code_agent_profile.py -q` | Manifest 发布门禁、契约/策略冻结与 Profile 回归 | 7 passed | ✅ |
| `python -m pytest tests/test_code_agent_control_plane.py -q` | 策略单向收紧与稳定拒绝原因 | 8 passed | ✅ |
| `python -m pytest tests/test_code_agent_control_plane.py tests/test_code_agent_profile.py tests/test_react_engine_v4.py -q` | Code 项目 ACL/入口门禁与 Standard Agent 回归 | 29 passed | ✅ |
| `python -m pytest tests/test_code_agent_runtime_context.py tests/test_code_agent_control_plane.py tests/test_single_loop_e2e.py -q` | Code Context 编译、控制面与统一循环回归 | 16 passed | ✅ |
| `python -m pytest tests/test_code_agent_runtime_context.py tests/test_single_loop_e2e.py -q` | Code Profile 统一事件 envelope 与 Standard loop 回归 | 6 passed | ✅ |
| `python -m pytest tests/test_code_agent_runtime_context.py tests/test_single_loop_e2e.py tests/test_code_agent_control_plane.py -q` | Code 正常/异常/取消/超时终结、清理失败与后续工具阻断 | 22 passed | ✅ |
| `python -m pytest tests/test_code_agent_standard_compat.py tests/test_single_loop_e2e.py tests/test_code_agent_profile.py tests/test_react_engine_v4.py -q` | Standard 默认 Profile、工具路由、终态和事件兼容性 | 23 passed | ✅ |
| `python -m pytest tests/test_code_agent_workspace.py tests/test_code_agent_runtime_context.py tests/test_code_agent_standard_compat.py -q` | 固定 commit、禁用仓库执行、Workspace 写隔离与 Runtime 接入 | 12 passed | ✅ |
| `python -m pytest tests/test_code_agent_runner.py tests/test_code_agent_runtime_context.py tests/test_code_agent_control_plane.py tests/test_code_agent_workspace.py -q` | Runner 资源/挂载/断网/镜像事实及危险能力拒绝 | 26 passed | ✅ |
| `python -m pytest tests/test_code_agent_workspace.py tests/test_code_agent_runner.py tests/test_code_agent_runtime_context.py -q` | Workspace 基线漂移、授权写入与外部污染门禁 | 17 passed | ✅ |
| `python -m pytest tests/test_code_agent_workspace.py tests/test_code_agent_runtime_context.py tests/test_code_agent_runner.py -q` | Workspace 只读留存、不可下载、到期清除及统一 lifecycle | 18 passed | ✅ |
| `python -m pytest tests/test_code_agent_tools.py tests/test_code_agent_runtime_context.py tests/test_single_loop_e2e.py tests/test_code_agent_control_plane.py -q` | read/search/edit/test 路径、预算、审计、冻结验证与统一循环 | 25 passed | ✅ |
| `python -m pytest tests/test_code_agent_tools.py tests/test_code_agent_control_plane.py tests/test_code_agent_runtime_context.py tests/test_single_loop_e2e.py -q` | shell 完整命令 allowlist、逃逸拒绝、Git 只读固定操作与统一 Runtime 回归 | 27 passed | ✅ |
| `python -m pytest tests/test_code_agent_tools.py tests/test_code_agent_runtime_context.py tests/test_single_loop_e2e.py -q` | 确定性预检、拒绝脱敏、仓库文本不可提权和统一循环回归 | 19 passed | ✅ |
| `python -m pytest tests/test_code_agent_tools.py tests/test_code_agent_runtime_context.py tests/test_single_loop_e2e.py tests/test_code_agent_control_plane.py -q` | 工具输出秘密分类/脱敏、项目 ACL 工件门禁与 Runtime 回归 | 32 passed | ✅ |
| `python -m pytest tests/test_code_agent_verifier.py tests/test_code_agent_runtime_context.py tests/test_code_agent_control_plane.py tests/test_code_agent_tools.py -q` | 强制 Verifier、冻结测试、路径/秘密/测试完整性门禁和 FINAL 不可绕过 | 34 passed | ✅ |
| `python -m pytest tests/test_code_agent_verifier.py tests/test_code_agent_runtime_context.py tests/test_code_agent_workspace.py tests/test_code_agent_control_plane.py -q` | 验证基线记录/恢复、新增与既有失败分类、环境不可用非成功 | 32 passed | ✅ |
| `python -m pytest tests/test_code_agent_artifacts.py tests/test_code_agent_verifier.py tests/test_code_agent_runtime_context.py tests/test_code_agent_runner.py tests/test_code_agent_workspace.py -q` | canonical diff、完整工件哈希、原子封存/回滚和 Runtime lifecycle | 29 passed | ✅ |
| `python -m pytest tests/test_code_agent_results.py tests/test_code_agent_artifacts.py tests/test_code_agent_runtime_context.py tests/test_code_agent_tools.py tests/test_code_agent_control_plane.py -q` | 稳定终态、session/project ACL API、可采用性门禁与空 diff 结果 | 36 passed | ✅ |
| `npm run build`（`apps/web`） | AgentChat Code 结果横幅与不可采用警告可生产编译 | built in 4.38s | ✅ |
| `python -m pytest tests/test_code_agent_artifacts.py tests/test_code_agent_results.py tests/test_code_agent_runtime_context.py tests/test_code_agent_control_plane.py -q` | 工件审阅/下载、哈希复核、stale 拒绝、接受引用与无 Git 写入 | 29 passed | ✅ |
| `npm run build`（`apps/web`） | Code 工件审阅对话框、固定下载与接受操作可生产编译 | built in 4.07s | ✅ |
| `python -m pytest tests/test_code_agent_control_plane.py tests/test_code_agent_profile.py tests/test_code_agent_standard_compat.py tests/test_react_engine_v4.py -q` | 六粒度 kill switch、新 run 拒绝、既有 run 与 Standard 可用性 | 38 passed | ✅ |
| `python -m pytest tests/test_code_agent_control_plane.py tests/test_code_agent_verifier.py tests/test_code_agent_artifacts.py tests/test_code_agent_tools.py tests/test_code_agent_runtime_context.py tests/test_code_agent_results.py -q` | 独立队列、并发/文件/diff 配额、预算可观察性与显式耗尽终态 | 57 passed | ✅ |
| `python -m pytest tests/test_code_agent_security.py tests/test_code_agent_tools.py tests/test_code_agent_verifier.py tests/test_code_agent_workspace.py tests/test_code_agent_runner.py tests/test_code_agent_artifacts.py tests/test_code_agent_runtime_context.py -q` | 注入、越权、秘密、网络/命令逃逸、保护测试、取消与污染负向矩阵 | 48 passed | ✅ |
| `python -m pytest tests/test_code_agent_evaluation.py tests/test_code_agent_results.py tests/test_code_agent_artifacts.py tests/test_code_agent_control_plane.py -q` | 三项目十任务计划、复现/接受/成本/耗时/清理观测与带哈希报告 | 32 passed | ✅ |
| `python -m pytest tests -q`（`apps/api`） | 完整后端与 Runtime/Standard/CodeAgent 回归 | 349 passed | ✅ |
| `npm run build`（`apps/web`） | 完整前端 production build | built in 4.49s | ✅ |
| `openspec validate code-agent-v1 --strict` | OpenSpec 变更严格校验 | valid | ✅ |
| `python -m pytest tests/test_code_agent_runtime_context.py tests/test_code_agent_standard_compat.py tests/test_single_loop_e2e.py -q` | 行尾清理后的 Unified Runtime / Standard 兼容复核 | 14 passed | ✅ |
| `git diff --check` | 变更无 whitespace error | clean | ✅ |
| `openspec instructions apply --change code-agent-v1 --json` | 正式任务全部完成 | `all_done`，26/26 | ✅ |
| `python -m pytest tests/test_code_agent_control_plane.py tests/test_code_agent_profile.py tests/test_code_agent_standard_compat.py -q` | task 7.1 admission 门禁及 Profile/Standard 兼容 | 29 passed | ✅ |
| `python -m pytest tests/test_code_agent_runner.py tests/test_code_agent_tools.py tests/test_code_agent_verifier.py tests/test_code_agent_runtime_context.py tests/test_code_agent_standard_compat.py -q` | task 7.2 真实 runner deadline、强制终止、三路径超时传播/清理及 Standard 兼容 | 40 passed | ✅ |
| `python -m py_compile app/services/code_agent/runner.py app/services/code_agent/tools.py app/services/code_agent/verifier.py app/services/agent_runtime/runtime.py` | task 7.2 修改模块可编译 | success | ✅ |
| 7.2 相关文件 `git diff --check` | 无 whitespace error | clean | ✅ |
| `python -m pytest tests/test_code_agent_workspace.py tests/test_code_agent_runner.py tests/test_code_agent_tools.py tests/test_code_agent_verifier.py tests/test_code_agent_runtime_context.py tests/test_code_agent_standard_compat.py -q` | task 7.3 首资源补偿、prepare/start/cleanup 失败终态及 7.2/Standard 回归 | 49 passed | ✅ |
| `python -m py_compile app/services/code_agent/workspace.py app/services/code_agent/runner.py app/services/agent_runtime/runtime.py` | task 7.3 修改模块可编译 | success | ✅ |
| 7.3 相关文件 `git diff --check` | 无 whitespace error | clean | ✅ |
| `python -m pytest tests/test_code_agent_*.py -q` | task 7.4 文本/跨块大文件/二进制/截断/失败/特殊格式负向扫描、sealer 精确内容证明及全部 CodeAgent 回归 | 98 passed | ✅ |
| `python -m py_compile app/services/code_agent/output_security.py app/services/code_agent/verifier.py app/services/code_agent/workspace.py app/services/code_agent/artifacts.py` | task 7.4 修改模块可编译 | success | ✅ |
| 7.4 相关文件 `git diff --check` | 无 whitespace error | clean | ✅ |
| `python -m pytest tests/test_code_agent_runtime_context.py tests/test_code_agent_runner.py tests/test_code_agent_workspace.py tests/test_code_agent_standard_compat.py tests/test_single_loop_e2e.py -q` | verification 后 7.3 外层补偿守卫、Context 构建失败及 Runtime/Standard 回归 | 32 passed | ✅ |
| `python -m pytest tests -q`（`apps/api`，7.3 verification 修复后） | 最终完整后端与 Runtime/Standard/CodeAgent 回归 | 369 passed，123 warnings | ✅ |
| `python -m pytest tests/test_code_agent_runtime_context.py tests/test_code_agent_standard_compat.py tests/test_single_loop_e2e.py -q`（最终） | Unified Runtime、Standard 默认行为与 single-loop 专项 | 20 passed，7 warnings | ✅ |
| `npm run build`（`apps/web`） | 最终前端 production build | built in 4.60s；仅既有 Rollup/chunk warnings | ✅ |
| `openspec validate code-agent-v1 --strict`（最终） | OpenSpec 变更严格校验 | valid | ✅ |
| `git diff --check`（最终） | 全仓变更无 whitespace error | clean | ✅ |
| `openspec instructions apply --change code-agent-v1 --json`（7.5 勾选后） | 正式任务全部完成 | `all_done`，31/31 | ✅ |

### Spec Scenario Traceability

| Delta spec | Scenarios | Primary executable evidence |
|---|---:|---|
| `agent-config` | 4 | `test_code_agent_profile.py`, `test_code_agent_control_plane.py`, `test_react_engine_v4.py` |
| `code-agent-profile` | 6 | `test_code_agent_control_plane.py`, `test_code_agent_runtime_context.py`, `test_code_agent_tools.py`, `test_code_agent_security.py` |
| `agent-runtime` | 5 | `test_code_agent_runtime_context.py`, `test_code_agent_runner.py`, `test_code_agent_tools.py`, `test_code_agent_standard_compat.py`, `test_single_loop_e2e.py` |
| `code-agent-workspace` | 5 | `test_code_agent_workspace.py`, `test_code_agent_runner.py`, `test_code_agent_runtime_context.py`, `test_code_agent_security.py` |
| `code-agent-project-policy` | 6 | `test_code_agent_control_plane.py`, `test_code_agent_tools.py`, `test_code_agent_security.py`, `test_code_agent_results.py` |
| `code-agent-verification` | 6 | `test_code_agent_verifier.py`, `test_code_agent_artifacts.py`, `test_code_agent_results.py` |

### Change Re-verification: 2026-08-23

| Dimension | Final status | Evidence |
|---|---|---|
| Completeness | 31/31 tasks；17/17 Requirements | `tasks.md`、六份 delta specs、CLI apply context |
| Correctness | 17/17 Requirements；32/32 Scenarios covered | 369 项完整后端、98 项 CodeAgent 专项及逐场景测试映射 |
| Coherence | 8/8 design decisions followed | 单一 ReAct loop、冻结控制面、Workspace/runner、typed tools、Verifier/sealer、统一事件、灰度和四项 fail-closed 边界 |

- **CRITICAL:** 0（审计期间发现的 runner→Context 空窗已修复并通过新增负向测试）。
- **WARNING:** 0。
- **SUGGESTION:** 0（既有 Pydantic deprecation 与 Rollup chunk 警告不构成本 change 的 spec/design 偏差）。
- **Final assessment:** All checks passed. Ready for archive after explicit archive request；本次未执行 archive。

### Verification Run: 2026-08-23

- 对 26/26 tasks、17 个 Requirement、28 个 Scenario 和 design 的 7 项核心决策完成 Completeness/Correctness/Coherence 核验。
- Completeness：26/26 tasks 已勾选；17/17 Requirement、28/28 Scenario 均找到实现或测试映射。
- Correctness：发现 4 个必须在 archive 前修复的 CRITICAL，涉及大文件秘密检测、internal non-production admission、runner 强制超时及启动失败清理。
- Coherence：统一 Runtime、显式 Profile、独立 Workspace、typed tools、Verifier/sealing、只读 Git 和 Standard 默认兼容等主体设计一致；上述 4 项是安全边界/生命周期上的实质偏差。

| Verification evidence | Actual | Status |
|---|---|---|
| `python -m pytest tests -q`（`apps/api`） | 349 passed，123 warnings，10.44s | ✅ |
| `npm run build`（`apps/web`） | built in 4.75s；仅既有 Rollup/chunk warnings | ✅ |
| `openspec validate code-agent-v1 --strict` | valid | ✅ |
| 2,000,037-byte secret-bearing file probe | Verifier 错误返回 `verification_passed` | ❌ CRITICAL |
| `production` project admission probe | 错误创建 `pending` Code run | ❌ CRITICAL |
| runner exec deadline probe | `timeout_seconds=0` 仍同步等待并成功返回 | ❌ CRITICAL |
| runner-start failure cleanup probe | run 保持 `pending`、retention 未调用、Workspace 仍存在且可写 | ❌ CRITICAL |

### OpenSpec Update Validation: 2026-08-23

| Check | Actual | Status |
|---|---|---|
| `openspec validate code-agent-v1 --strict` | updated change valid | ✅ |
| `openspec instructions apply --change code-agent-v1 --json` | `ready`，26/31 complete，5 remaining | ✅ |
| `git diff --check` | clean | ✅ |

### Errors

| Error | Resolution |
|---|---|
| 全局 catch-up 脚本路径不存在 | 使用仓库本地 catch-up 脚本，成功完成且无恢复输出。 |
| 初始模板替换补丁格式/上下文不匹配 | 删除模板并使用完整文件补丁重建。 |
| 首次定向测试路径与 `apps/api` 工作目录重复 | 未执行任何测试；改用 `tests/...` 相对路径重跑。 |
| 1.4 首轮定向测试有两个夹具假设过期 | 直接调用的 FastAPI `Query` 参数需传 `None`；Code Profile 保存现要求项目授权，已将旧 Profile 测试改为模型序列化覆盖。 |
| 2.2 首轮 Standard Runtime 回归的测试 Context 不含新字段 | Code payload 发布器改为通过 `getattr` 兼容旧 Context 形态，视其为 Standard。 |
| 2.3 首轮事件测试仍只期望 prepare/terminate | 清理阶段现已成为正式 Profile 事件；更新断言并补充取消/超时清理测试。 |
| 2.3 第二轮取消/超时测试 monkeypatch 方法签名缺少 `self` | 修正测试替身签名；生产路径无需变更。 |
| 3.2 依赖搜索命令中的未匹配 shell glob 导致 zsh 提示 | 已从既有 `docker_service.py` 确认 Docker client 接入点；不再使用未解析 glob。 |
| 3.4 首轮到期清除因留存目录已只读而失败 | 在已校验的 run 根目录内恢复所有者权限后执行清除。 |
| 4.1 首轮 Runtime Context 测试夹具缺少冻结 `allowed_tools` | 补齐测试 run 的完整有效策略；保持生产逻辑对缺失能力默认拒绝。 |
| 5.3 首轮提交失败回滚测试遗留只读临时工件目录 | 在已验证的 run 工件目录内恢复所有者权限后清除未提交集合，保持原子封存语义。 |
| 5.4 首个多文件补丁 hunk 边界格式无效 | 工具整体拒绝且未产生部分修改；拆分为小补丁后成功应用。 |
| 5.5 首轮下载接口测试将 `FileResponse.path` 的 `Path` 与字符串比较 | 实际下载目标正确；修正测试为同类型路径比较，生产实现无需改动。 |
| 6.2 首个多文件补丁再次出现 hunk 边界格式无效 | 工具整体拒绝且未产生部分修改；继续按文件拆分后成功应用。 |
| 6.5 根级 `pytest` 额外收集 `scripts/smoke_test.py` 并尝试连接 live API | 单元测试尚未执行即在 smoke 导入期遇到沙箱网络拒绝；完整后端回归改为仓库正式 `tests/` 目录，live smoke 作为环境限制单列。 |
| 6.5 `git diff --check` 发现 Runtime 既有改动区空白行尾随空格 | 仅机械移除行尾空格；随后 diff check 与 Runtime 专项 14 项均通过。 |
| 7.1 首轮新测试插入到既有 missing/unpublished Manifest 测试中间 | 生产门禁断言已通过；恢复原测试后半段到正确函数并重跑，29 项全部通过。 |
| 7.2 兼容性补测引用不存在的 `tests/test_agent_runtime.py` | pytest 在收集前退出且未执行测试；使用实际存在的 `test_code_agent_standard_compat.py` 并与四组 task 7.2 测试合并重跑，40 项通过。 |
| 7.4 首轮大文件秘密用例把 `sk-...` 直接拼在字母后，不符合既有 token boundary | 保持生产匹配规则；将秘密放在换行后的 token 边界并刻意横跨 2 MiB/1 MiB 分块边界，重跑专项 28 项及全部 CodeAgent 98 项均通过。 |
| 1.2 回归首轮测试在 `apps/api` 工作目录重复 `.venv` 路径 | 测试未执行；改用目录内解释器后发现该 venv 未安装 pytest，最终使用已配置的 `pytest` 入口完成验证。 |
| 迁移回归测试的设置替身缺少 `data_dir` | 补齐启动例程所需字段；生产迁移已执行，测试随后通过。 |
