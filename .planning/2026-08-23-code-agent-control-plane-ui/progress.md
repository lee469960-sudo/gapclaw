# Progress Log: code-agent-control-plane-ui

## Session: 2026-08-23

### Current Status

- **Phase:** All phases completed
- **OpenSpec tasks completed:** 16/16
- **Current formal task:** none
- **Implementation status:** tasks 1.1–5.2、verification remediation、spec sync 与 archive 均已完成。

### Actions Taken

- archive：用户选择先同步 delta specs；`agent-config` 智能追加 Code Project 绑定 Requirement，并创建 `code-agent-control-plane`、`code-agent-operator-ui` 两份主规范。
- archive：主 specs 校验 6 passed/0 failed，三项 capability 逐块比较一致且无 delta 标记；change 移至 `openspec/changes/archive/2026-08-23-code-agent-control-plane-ui/`。
- archive 后复核：active change 列表不再包含本 change；归档内 16/16 tasks 完整；`git diff --check` clean。无不完整 artifact/task warning。
- verification remediation：Manifest 发布在修改草稿状态前复检项目 environment tier，不支持环境返回稳定原因且不替换当前 published 版本。
- verification remediation：Agent 编辑器打开现有记录时强制刷新 refs 和 Agent detail，以最新服务端 availability 填充项目状态，不再依赖列表行快照。
- verification remediation：Manifest 的 validation plan、policy、budgets 使用严格 JSON 解析与类型校验；错误就地展示并阻止提交，不再静默回退为空数组/对象。
- remediation 验证完成：控制面定向测试 30 passed；完整后端 400 passed；前端 production build、Python 编译、OpenSpec strict validate、16/16 all_done 核对及全仓 diff check 均通过。正式 `tasks.md` 未修改。
- task 1.1：新增 Code Project/Manifest 控制面请求与响应 schema、稳定错误原因和专用 action-CGI router；项目创建在任何持久化前校验必填名称、环境 tier、visibility、页面权限和名称冲突。
- task 1.1：新增 schema/API 单测，证明缺失名称、非法 tier 和无页面权限请求不会产生部分 `CodeProject` 行；响应 schema 同时覆盖项目 availability 与不完整 Manifest 草稿的字段级错误结构。
- task 1.1 验证完成：`pytest tests/test_code_agent_control_plane_ui.py -q` → 5 passed；相关模块 `py_compile` 成功；相关 `git diff --check` clean。可以勾选 task 1.1。
- task 1.1 已在上述进度与验证记录完成后勾选；正式进度现为 1/16。
- task 1.2：将 `/pages/page_code_project.cgi` 注册到 FastAPI、页面菜单映射和 `ALL_PAGES`；沿用页面权限中间件，并在 handler 内保留服务端页面权限复检。
- task 1.2：确认内置 `master`/`admin` 获得新页面权限、普通 `user` 不自动获得；startup 对既有内置管理角色的权限集合会重新同步。无权限主体无法写入或枚举项目。
- task 1.2 验证完成：控制面 UI 定向套件 7 passed；相关 `git diff --check` clean。可以勾选 task 1.2。
- task 1.2 已在上述进度与验证记录完成后勾选；正式进度现为 2/16。
- task 1.3：在既有 control-plane service 实现服务端 availability read model，稳定区分 `ready`、`project_disabled`、`project_environment_not_allowed`、`manifest_missing` 与 `manifest_invalid`，并携带不可用 detail 或当前 published Manifest 事实。
- task 1.3：read model 只读取项目/Manifest，不创建 Code run；run admission 仍重新查询项目状态与 Manifest。逐状态及状态变化后 admission 复检测试已覆盖。
- task 1.3 验证完成：新旧控制面组合回归 36 passed；相关模块编译与 diff check 通过。可以勾选 task 1.3。
- task 1.3 已在上述进度与验证记录完成后勾选；Phase 1 完成，正式进度现为 3/16。
- task 2.1：补齐 Code Project 可见列表、详情、更新和启停 API；所有读取/写入同时要求页面权限与项目 ACL，无权详情/写入统一返回不泄露存在性的 `code_project_not_found`。
- task 2.1：创建者和显式授权用户的可见性/写权限、无权用户不可枚举/修改、停用后的 availability 均有定向覆盖。
- task 2.1 验证完成：新旧控制面组合回归 37 passed；相关 diff check clean。可以勾选 task 2.1。
- task 2.1 已在上述进度与验证记录完成后勾选；正式进度现为 4/16。
- task 2.2：实现 Manifest draft 保存/更新与读取；新草稿使用项目下一版本号，允许字段不完整并返回 repository/base commit/paths/validation/image/tools/budgets 的字段级稳定错误。
- task 2.2：draft 不会被 `_published_manifest` 选中，未发布状态下 `create_code_run` 仍返回 `manifest_missing` 且不创建 run。
- task 2.2 验证完成：新旧控制面组合回归 38 passed；router 编译及相关 diff check 通过。可以勾选 task 2.2。
- task 2.2 已在上述进度与验证记录完成后勾选；正式进度现为 5/16。
- task 2.3：实现完整草稿的 admission 级重验、发布、只读 published 历史和 published 原地编辑拒绝；新发布版本保留旧版本历史，run admission 按最高 published 版本冻结。
- task 2.3：验证失败与持久化 commit 异常均不会替换当前 published 版本；事务 rollback 后候选仍为 draft，readiness 继续引用旧版本。
- task 2.3 验证完成：新旧控制面组合回归 41 passed；router 编译及 diff check 已通过。可以勾选 task 2.3。
- task 2.3 已在上述进度与验证记录完成后勾选；正式进度现为 6/16。
- task 2.4：新增 `CodeControlAudit` 持久化模型，项目创建/更新/启停、Manifest 草稿保存和发布均在同一事务写入 actor/action/project/manifest/details 审计；失败事务不会留下成功审计。
- task 2.4：回归确认后续项目/草稿写入不会修改既有 Code run 的 frozen `task_contract`/`manifest_id`，并联合执行 Standard Profile 兼容测试。
- task 2.4 验证完成：控制面、既有 Code 控制面和 Standard 兼容组合 44 passed；模型/router 编译及 diff check 通过。可以勾选 task 2.4。
- task 2.4 已在上述进度与验证记录完成后勾选；Phase 2 完成，正式进度现为 7/16。
- task 3.1：新增 `CodeProjects.vue`、`/code-projects` SPA 路由和角色权限 UI 项；导航继续由服务端 menu/RBAC 生成。页面在 ACL 可见范围内支持项目列表、创建、编辑和启停，并展示 availability 与 API 错误。
- task 3.1：新增前端组件/API 接线契约测试，连同后端真实 handler 测试验证项目 actions、状态展示和错误入口；Vite production build 成功生成独立 CodeProjects chunk。
- task 3.1 验证完成：控制面 UI 套件 20 passed；`npm run build` 成功；相关 diff check clean。可以勾选 task 3.1。
- task 3.1 已在上述进度与验证记录完成后勾选；正式进度现为 8/16。
- task 3.2：Manifest drawer 按仓库/基线、路径/验证、镜像/工具、策略/预算分组；保存后逐字段展示服务端 `validation_errors`，不在浏览器复制权威 Manifest 校验。
- task 3.2：不完整草稿保持可保存；没有 draft 或存在字段错误时发布按钮保持禁用。组件契约测试与后端不完整草稿 API 测试共同覆盖。
- task 3.2 验证完成：控制面 UI 套件 22 passed；Vite production build 成功。可以勾选 task 3.2。
- task 3.2 已在上述进度与验证记录完成后勾选；正式进度现为 9/16。
- task 3.3：Manifest 发布动作包含不可变提示确认；成功后关闭 drawer 并重新加载项目列表/readiness，published 历史按版本倒序只读展示，并可据当前版本开始新修订。
- task 3.3：组件/API 契约测试覆盖 publish/history/readiness refresh 接线；后端验证失败及 commit 失败测试确认当前 published 版本不被覆盖。22 项 UI 契约测试与 production build 已通过，可以勾选 task 3.3。
- task 3.3 已在上述进度与验证记录完成后勾选；Phase 3 完成，正式进度现为 10/16。
- task 4.1：Agent refs 的 Code Project 候选继续限定 ACL+enabled，并增加服务端 availability；Code Agent list/detail 增加授权范围内的项目名称和 readiness。
- task 4.1：无权绑定不返回项目名称，Standard Agent list/detail 不增加 Code 专属字段；存量启动迁移 refs 测试已更新并保持通过。
- task 4.1 验证完成：控制面 UI、startup migration、Profile 与 Standard 兼容组合 30 passed；Agent router 编译及 diff check 通过。可以勾选 task 4.1。
- task 4.1 已在上述进度与验证记录完成后勾选；正式进度现为 11/16。
- task 4.2：Agent 创建/编辑表单新增显式 Standard/Code Profile；Code 条件分支只列当前用户有权的候选项目，禁用非 ready 选项并展示稳定 availability 原因与 Code Projects 管理入口。
- task 4.2：前端提交 `profile`/`code_project_id`，Standard 默认且切回 Standard 时显式清空旧绑定；浏览器在 Code 项目未选择或非 ready 时阻止提交，服务端在任何 Agent 字段写入前再次执行 ACL 与 readiness 校验。
- task 4.2：真实 handler 测试证明现有 Standard Agent 可在发布 Manifest 后切换为 Code；缺失项目返回 `code_project_required`、未发布项目返回 `manifest_missing` 且名称/描述/Profile/绑定均不发生部分更新；未声明 Profile 的创建仍为 Standard。
- task 4.2 验证完成：CodeAgent 控制面/Profile/Standard 组合回归 54 passed；Vite production build 成功；Agent router 编译与相关 diff check 通过。实现与测试均确认完成，可以勾选 task 4.2。
- task 4.2 已在上述进度与验证记录完成后勾选；正式进度现为 12/16。
- task 4.3：Agent 卡片新增 Standard/Code Agent 标识；Code Agent 同时显示绑定项目名和服务端 Manifest readiness，失效配置显示具体原因及“修复项目配置”入口。
- task 4.3：编辑器保留 task 4.2 的状态提示和管理入口；当既有绑定因停用、失效或 ACL 变化不再出现在 enabled refs 中时，追加只读禁用的当前绑定选项，确保项目/Manifest 原因仍可见且不可误保存。
- task 4.3 验证完成：控制面 UI、Profile 与 Standard 组合 32 passed；Vite production build 成功；相关 diff check clean。代码与测试均确认完成，可以勾选 task 4.3。
- task 4.3 已在上述进度与验证记录完成后勾选；正式进度现为 13/16。
- task 4.4：Agent Chat 新增 Code Agent 当前项目与 Manifest readiness 条；发送前刷新 Agent 状态，失效时显示稳定原因和 Code Projects 管理入口并阻止提交，不静默降级为 Standard。
- task 4.4：Code result API 增加冻结 Manifest 版本和结构化 Verifier 报告；Chat 对所有终态展示稳定 label、Verifier/封存状态和可展开证据，非可采用结果保留明确警告。
- task 4.4：审阅/下载/接受入口仅在 `directly_adoptable`（`patch_ready` + Verifier passed + sealed）时呈现且客户端函数再设 guard；后端 review/download 同样拒绝非 `patch_ready` 或非当前工件，返回 `code_artifact_not_reviewable`。
- task 4.4 验证完成：控制面 UI、结果、工件、Profile 与 Standard 组合 44 passed；Vite production build 成功；相关 Python 模块编译与 diff check 通过。代码与测试均确认完成，可以勾选 task 4.4。
- task 4.4 已在上述进度与验证记录完成后勾选；Phase 4 完成，正式进度现为 14/16。
- task 5.1：新增单库真实 handler 端到端场景，串联 Code Project 创建、完整 Manifest 草稿保存、原子发布、现有 Standard Agent 切换为 Code 和 Agent Chat 提交；断言 run 冻结正确 Manifest id/version/objective 并进入 `_enqueue_code_chat` 专用队列。
- task 5.1：同场景覆盖项目 ACL 外用户提交、项目停用后提交和强制陈旧绑定到未发布项目；分别返回 `code_project_unauthorized`、admission `project_unavailable`、`manifest_missing`，且三个失败分支均不创建额外 Code run。
- task 5.1 验证完成：控制面 UI、既有控制面、Profile 与 Standard 组合 57 passed；相关 diff check clean。代码和测试均确认完成，可以勾选 task 5.1。
- task 5.1 已在上述进度与验证记录完成后勾选；正式进度现为 15/16。
- task 5.2：完整后端套件最终重跑 398 passed；单独 Standard/CodeAgent Profile、控制面、结果与兼容集合 60 passed，确认统一 ReAct runtime 的 Standard 路径无回归。
- task 5.2：前端 production build 成功；全仓 `git diff --check` clean；`openspec validate code-agent-control-plane-ui --strict` valid。构建仅保留既有 Rollup PURE annotation 与大 chunk warnings，测试仅保留既有 Pydantic/UTC deprecation warnings。
- task 5.2：三份 delta specs 的 17 个 Scenario 已全部映射到真实 handler、领域服务、组件契约或兼容测试，追溯表记录为 17/17。实现、完整验证与 Scenario 追溯均确认完成，可以勾选 task 5.2。
- task 5.2 已在上述进度与验证记录完成后勾选；Phase 5 与全部 OpenSpec apply tasks 完成，正式进度为 16/16。
- 最终 OpenSpec 状态复核：`openspec instructions apply --change code-agent-control-plane-ui --json` 返回 `state=all_done`、16/16 complete、0 remaining；strict validate 与最终全仓 `git diff --check` 再次通过。
- 完整读取 `proposal.md`、`design.md`、三份 `specs/*/spec.md` 和 `tasks.md`。
- 创建独立规划目录 `.planning/2026-08-23-code-agent-control-plane-ui/`。
- 将 `tasks.md` 的 16 项正式任务映射为五个 execution phases；未重新定义需求或创建额外正式任务。
- 建立 `findings.md` 记录执行发现，建立本文件记录实际完成情况。
- 将 `.planning/.active_plan` 切换为本 change。
- 未修改业务代码、OpenSpec artifacts 或 `tasks.md` 勾选。
- `opsx:apply` 已启动；OpenSpec CLI 确认 `state=ready`、0/16，本轮从 task 1.1 开始。

### Test Results

| Test | Expected | Actual | Status |
|---|---|---|---|
| `pytest tests/test_code_agent_control_plane_ui.py -q`（verification remediation） | 三项 warning 的 handler/组件契约回归 | 30 passed，44 warnings | ✅ |
| `pytest tests -q`（`apps/api`，remediation 完整回归） | 修复后无后端回归 | 400 passed，180 个既有 warnings | ✅ |
| `npm run build`（`apps/web`，remediation） | 严格 JSON 与最新 detail 接线可生产编译 | built in 4.05s；仅既有 chunk-size warning | ✅ |
| remediation `py_compile` / OpenSpec strict validate / 16/16 状态 / `git diff --check` | 编译、规范、任务状态与 whitespace 均有效 | success / valid / all_done / clean | ✅ |
| `openspec validate code-agent-control-plane-ui --strict` | OpenSpec artifacts 有效 | valid | ✅ |
| `openspec instructions apply --change code-agent-control-plane-ui --json` | 正式任务状态可读取 | `ready`，0/16 complete | ✅ |
| `pytest tests/test_code_agent_control_plane_ui.py -q` | task 1.1 schema/API 原子失败与响应契约 | 5 passed，2 warnings | ✅ |
| `python -m py_compile app/routers/code_project.py app/services/code_agent/control_plane.py` | task 1.1 修改模块可编译 | success | ✅ |
| task 1.1 相关 `git diff --check` | 无 whitespace error | clean | ✅ |
| `pytest tests/test_code_agent_control_plane_ui.py -q`（task 1.2） | router/menu/RBAC、角色同步、无权枚举拒绝 | 7 passed，3 warnings | ✅ |
| task 1.2 相关 `git diff --check` | 无 whitespace error | clean | ✅ |
| `pytest tests/test_code_agent_control_plane_ui.py tests/test_code_agent_control_plane.py -q` | task 1.3 availability 状态、无副作用及 admission 独立复检 | 36 passed，19 warnings | ✅ |
| task 1.3 模块编译与相关 `git diff --check` | 可编译且无 whitespace error | success / clean | ✅ |
| `pytest tests/test_code_agent_control_plane_ui.py tests/test_code_agent_control_plane.py -q`（task 2.1） | 项目 CRUD/启停、页面 RBAC 与 ACL 边界 | 37 passed，22 warnings | ✅ |
| task 2.1 相关 `git diff --check` | 无 whitespace error | clean | ✅ |
| `pytest tests/test_code_agent_control_plane_ui.py tests/test_code_agent_control_plane.py -q`（task 2.2） | 草稿保存/读取、字段错误和未发布门禁 | 38 passed，23 warnings | ✅ |
| task 2.2 router 编译与相关 `git diff --check` | 可编译且无 whitespace error | success / clean | ✅ |
| `pytest tests/test_code_agent_control_plane_ui.py tests/test_code_agent_control_plane.py -q`（task 2.3） | 原子发布、不可变历史与失败保留 | 41 passed，28 warnings | ✅ |
| `pytest tests/test_code_agent_control_plane_ui.py tests/test_code_agent_control_plane.py tests/test_code_agent_standard_compat.py -q` | task 2.4 审计、冻结契约和 Standard 兼容 | 44 passed，43 warnings | ✅ |
| task 2.4 模型/router 编译与相关 `git diff --check` | 可编译且无 whitespace error | success / clean | ✅ |
| `pytest tests/test_code_agent_control_plane_ui.py -q`（task 3.1） | 管理页面与 API actions/状态/错误接线 | 20 passed，27 warnings | ✅ |
| `npm run build`（`apps/web`，task 3.1） | 新页面、路由与导航可生产编译 | built in 3.96s；仅既有 Rollup/chunk warnings | ✅ |
| task 3.1 相关 `git diff --check` | 无 whitespace error | clean | ✅ |
| `pytest tests/test_code_agent_control_plane_ui.py -q`（tasks 3.2/3.3） | 分组表单、字段错误、发布禁用/确认与历史接线 | 22 passed，27 warnings | ✅ |
| `npm run build`（`apps/web`，tasks 3.2/3.3） | Manifest UI 可生产编译 | built in 4.27s；仅既有 Rollup/chunk warnings | ✅ |
| `pytest tests/test_code_agent_control_plane_ui.py tests/test_startup_migrations.py tests/test_code_agent_profile.py tests/test_code_agent_standard_compat.py -q` | task 4.1 refs/list/detail、ACL 与存量兼容 | 30 passed，43 warnings | ✅ |
| task 4.1 Agent router 编译与相关 `git diff --check` | 可编译且无 whitespace error | success / clean | ✅ |
| `pytest tests/test_code_agent_control_plane_ui.py tests/test_code_agent_control_plane.py tests/test_code_agent_profile.py tests/test_code_agent_standard_compat.py -q` | task 4.2 Profile/项目提交、切换、fail-closed 与 Standard 默认 | 54 passed，48 warnings | ✅ |
| `npm run build`（`apps/web`，task 4.2） | Agent Profile/项目编辑器可生产编译 | built in 3.69s；仅既有 Rollup/chunk warnings | ✅ |
| task 4.2 Agent router 编译与相关 `git diff --check` | 可编译且无 whitespace error | success / clean | ✅ |
| `pytest tests/test_code_agent_control_plane_ui.py tests/test_code_agent_profile.py tests/test_code_agent_standard_compat.py -q` | task 4.3 卡片/编辑页 Profile、项目、Manifest 状态和修复入口 | 32 passed，32 warnings | ✅ |
| `npm run build`（`apps/web`，task 4.3） | Agent 状态呈现可生产编译 | built in 4.06s；仅既有 Rollup/chunk warnings | ✅ |
| task 4.3 相关 `git diff --check` | 无 whitespace error | clean | ✅ |
| `pytest tests/test_code_agent_control_plane_ui.py tests/test_code_agent_results.py tests/test_code_agent_artifacts.py tests/test_code_agent_profile.py tests/test_code_agent_standard_compat.py -q` | task 4.4 终态、Verifier 证据、sealed artifact 与 patch_ready-only 审阅 | 44 passed，37 warnings | ✅ |
| `npm run build`（`apps/web`，task 4.4） | Agent Chat Code 状态/结果呈现可生产编译 | built in 4.07s；仅既有 Rollup/chunk warnings | ✅ |
| task 4.4 相关 Python 编译与 `git diff --check` | 可编译且无 whitespace error | success / clean | ✅ |
| `pytest tests/test_code_agent_control_plane_ui.py tests/test_code_agent_control_plane.py tests/test_code_agent_profile.py tests/test_code_agent_standard_compat.py -q` | task 5.1 完整控制面链路与三类 fail-closed 分支 | 57 passed，60 warnings | ✅ |
| task 5.1 相关 `git diff --check` | 无 whitespace error | clean | ✅ |
| `pytest tests -q`（`apps/api`，task 5.2 最终重跑） | 完整后端测试无回归 | 398 passed，178 warnings | ✅ |
| Standard/CodeAgent 明确兼容集合 | Profile、控制面、结果与 Standard runtime 均通过 | 60 passed，60 warnings | ✅ |
| `npm run build`（`apps/web`，task 5.2） | production build 成功 | built in 3.97s；仅既有 Rollup/chunk warnings | ✅ |
| `git diff --check`（全仓） | 无 whitespace error | clean | ✅ |
| `openspec validate code-agent-control-plane-ui --strict` | change artifacts strict valid | valid | ✅ |
| 尚未执行实现测试 | 初始化阶段不执行 implementation 验证 | 未运行 | — |

### Scenario Traceability（17/17）

| Spec Scenario | Verification evidence |
|---|---|
| agent-config / 保存授权项目绑定 | `test_agent_editor_save_switches_existing_agent_only_when_project_is_ready`、`test_control_plane_end_to_end_enables_existing_agent_and_fails_closed` |
| agent-config / 保存未授权或停用项目 | `test_code_profile_configuration_requires_an_authorized_project`、`test_agent_editor_save_switches_existing_agent_only_when_project_is_ready` |
| agent-config / 存量请求不受影响 | `test_agent_create_without_profile_persists_standard`、`test_legacy_agent_without_materialized_profile_runs_as_standard` |
| code-agent-control-plane / 管理员创建内部非生产项目 | `test_project_create_schema_and_response_contract`、`test_control_plane_end_to_end_enables_existing_agent_and_fails_closed` |
| code-agent-control-plane / 无权用户不能管理项目 | `test_user_without_page_permission_cannot_write_or_enumerate_projects`、`test_project_list_detail_update_and_enable_respect_page_and_project_acl` |
| code-agent-control-plane / 停用项目阻止新运行 | `test_control_plane_end_to_end_enables_existing_agent_and_fails_closed` |
| code-agent-control-plane / 保存未完成草稿 | `test_incomplete_manifest_draft_is_saved_readable_and_not_runnable` |
| code-agent-control-plane / 发布有效 Manifest | `test_manifest_publish_is_versioned_historical_and_published_rows_are_immutable`、`test_control_plane_end_to_end_enables_existing_agent_and_fails_closed` |
| code-agent-control-plane / 发布无效 Manifest 被拒绝 | `test_failed_manifest_publish_preserves_current_published_version`、`test_manifest_publish_persistence_failure_rolls_back_atomically` |
| code-agent-control-plane / 项目已就绪 | `test_project_availability_has_stable_side_effect_free_states[ready-…]`、`test_agent_refs_list_and_detail_expose_only_authorized_project_readiness` |
| code-agent-control-plane / 项目未就绪 | `test_project_availability_has_stable_side_effect_free_states` 的 disabled/tier/missing/invalid 参数分支 |
| code-agent-operator-ui / 为现有 Agent 启用 Code Profile | `test_agent_editor_save_switches_existing_agent_only_when_project_is_ready`、`test_control_plane_end_to_end_enables_existing_agent_and_fails_closed` |
| code-agent-operator-ui / Code Profile 缺少项目 | `test_agent_editor_save_switches_existing_agent_only_when_project_is_ready`、`test_agent_editor_component_submits_profile_and_requires_ready_project` |
| code-agent-operator-ui / Standard Agent 保持默认行为 | `test_agent_editor_save_switches_existing_agent_only_when_project_is_ready`、`test_standard_runtime_emits_existing_done_event_without_profile_payload` |
| code-agent-operator-ui / 列表识别 Code Agent | `test_agent_refs_list_and_detail_expose_only_authorized_project_readiness`、`test_agent_cards_and_editor_show_code_project_manifest_status_and_repair_link` |
| code-agent-operator-ui / 显示可审阅结果 | `test_required_terminal_states_have_stable_presentation_and_only_verified_seal_is_adoptable`、`test_project_authorized_review_and_fixed_file_download_interfaces`、Agent Chat 组件契约测试 |
| code-agent-operator-ui / Manifest 或项目不可用 | `test_agent_chat_shows_code_context_verifier_evidence_and_only_reviews_patch_ready`、`test_control_plane_end_to_end_enables_existing_agent_and_fails_closed` |

### Task Completion Protocol

每完成一个 OpenSpec task，严格依次执行：

1. 在本文件记录实际代码改动与测试结果。
2. 确认该 task 的实现和所要求测试均完成。
3. 最后才在 `openspec/changes/code-agent-control-plane-ui/tasks.md` 勾选对应任务。

### Errors

| Error | Resolution |
|---|---|
| 初次人工汇总误记为 17 项正式任务 | CLI 校验显示 16 项；已同步纠正 task_plan/findings/progress。 |
| task 4.2 首个多文件补丁重复声明 `Agents.vue` | 工具整体拒绝、无代码写入；拆分补丁后继续。 |
| task 5.1 首次端到端测试将控制面 `project_disabled` 原因用于断言 run admission | 27 passed/1 failed；保留既有 admission 的 `project_unavailable` 契约，测试改为分别验证 read model 与 admission。 |

### Verification: 2026-08-23

- `openspec status --change code-agent-control-plane-ui --json`：`spec-driven`，planning/implementation complete。
- `openspec instructions apply --change code-agent-control-plane-ui --json`：16/16 complete，`state=all_done`。
- 首次完整后端验证：398 passed，178 个既有 deprecation warnings；remediation 后完整重跑：400 passed，180 个既有 deprecation warnings。
- remediation 后前端 production build：built in 4.05s，仅既有 chunk-size warning。
- `openspec validate code-agent-control-plane-ui --strict`：valid。
- `git diff --check`：clean。
- 最终验证结论：0 CRITICAL、0 WARNING、0 SUGGESTION；17/17 Scenarios 完整验证。3 个首次 verification warning 已修复，未修改 OpenSpec tasks。
