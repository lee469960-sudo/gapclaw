# Findings & Decisions: code-agent-control-plane-ui

## Requirements Source of Truth

- 正式实现任务：`openspec/changes/code-agent-control-plane-ui/tasks.md`。
- 行为验收：`openspec/changes/code-agent-control-plane-ui/specs/` 下的 delta specs。
- 架构与边界：`openspec/changes/code-agent-control-plane-ui/design.md`。
- 本文件只记录执行发现、风险与待决事项，不创建正式任务或重新定义需求。

## Confirmed Scope and Architecture

- 本 change 只补齐 CodeAgent 的可操作控制面，不改变既有 run admission、Workspace、runner、Verifier、artifact acceptance 或 Git 写入限制。
- Code Project 页面负责项目和 Manifest 生命周期；Agent 编辑器只消费紧凑的项目引用和服务端计算的 availability。
- 页面 RBAC 与项目 ACL 是两道独立门禁，所有动作以服务端校验为准。
- Manifest 使用 draft-to-publish：发布时完整重验并原子生成不可变版本，失败时旧 published 版本继续有效。
- Agent 仍使用统一模型；`standard` 保持默认，只有显式选择 `code` 时才要求绑定 ready 且有权访问的项目。

## Execution Findings

- OpenSpec `tasks.md` 共有 16 项正式任务，当前 0/16 完成；任务按原有五个章节映射为五个 execution phases，不新增任务。
- 三份 delta specs 共定义 6 个 Requirement、17 个 Scenario：`agent-config` 1/3、`code-agent-control-plane` 3/8、`code-agent-operator-ui` 2/6。
- `openspec instructions apply` 返回 `state=ready`、0/16；从 task 1.1 开始执行。
- 工作树包含 `code-agent-v1` 及其他既有未提交改动；本 change 必须在这些文件上做局部增量，不能清理、回滚或格式化无关改动。
- 既有控制面基础位于 `models.py` 的 `CodeProject`/`CodeProjectManifest` 与 `services/code_agent/control_plane.py`；目前只有 run admission/策略冻结服务，没有专用管理 router、管理 schema、项目/Manifest 序列化或 availability read model。
- 现有页面 API 使用 `/pages/page_*.cgi` 的 action 风格、`ok`/`fail` envelope、`get_session_user` 与页面权限中间件；新增接口应匹配该约定，避免引入第二种 API 风格。
- Agent router 已持久化 `profile`/`code_project_id` 并做项目 ACL 校验，但当前 Code 项目 refs 仅返回 `id/name`，且保存时没有显式拒绝停用项目；后续 tasks 4.1/4.2 需补齐 readiness 与状态门禁。

## Issues Encountered

| Issue | Resolution |
|---|---|
| 当前 active plan 仍为 `code-agent-v1` | 为本 change 建立独立计划目录，完成初始化后再切换 `.planning/.active_plan`，不覆盖旧计划。 |
| 初次人工计数把 16 项 tasks 误记为 17 项 | 使用 `openspec instructions apply` 的 0/16 结果和各 phase 数量复核后纠正；不影响任务映射或 OpenSpec 原文。 |
| task 4.2 首个补丁对 `Agents.vue` 声明了两次 Update File | `apply_patch` 整体拒绝且未写入；改为每个文件单一 Update File 的小补丁。 |
| task 5.1 首次端到端断言预期停用提交返回 `project_disabled`，实际 run admission 返回 `project_unavailable` | 这是既有运行时契约与控制面细分 read model 的有意边界：控制面显示 `project_disabled`，admission 独立复检并以 `project_unavailable` fail closed；不修改非目标运行时语义，修正测试分别断言两层原因。 |

## Resources

- `openspec/changes/archive/2026-08-23-code-agent-control-plane-ui/proposal.md`
- `openspec/changes/archive/2026-08-23-code-agent-control-plane-ui/design.md`
- `openspec/changes/archive/2026-08-23-code-agent-control-plane-ui/specs/`
- `openspec/changes/archive/2026-08-23-code-agent-control-plane-ui/tasks.md`

## Verification Findings: 2026-08-23

- Completeness：16/16 tasks checked；6/6 Requirements 均能映射到实现；无 CRITICAL。
- Correctness：首次验证发现的 3 个 WARNING 已全部修复；17/17 Scenario 均有实现与验证证据。
- Coherence：专用 Code Project 页面、双层 RBAC/ACL、draft-to-publish、服务端 availability、统一 Agent Profile 五项设计方向均已落地；发布期环境复检、编辑器 readiness 刷新和 Manifest JSON 输入保真均已补齐。

| Status | Finding | Resolution / Evidence |
|---|---|---|
| RESOLVED | 不支持环境 tier 的既有项目仍可发布 Manifest | publish handler 在任何 Manifest 状态修改前复检项目 tier，失败返回 `project_environment_not_allowed`；真实 handler 回归证明原 published 版本和 draft 状态均保持不变。 |
| RESOLVED | Agent 编辑器打开时可能展示陈旧 readiness | 打开既有 Agent 时先强制刷新 refs，再请求最新 detail，并以 detail 中的项目 availability 填充表单；组件契约测试和 production build 通过。 |
| RESOLVED | Manifest JSON 语法错误会被前端静默替换 | validation plan、policy、budgets 改为严格解析并校验数组/对象类型；字段内显示本地错误且阻止网络提交，不再回退为 `[]`/`{}`；组件契约测试和 production build 通过。 |

Re-verification gates：定向控制面测试 30 passed；`pytest tests -q` 400 passed；`npm run build` built in 4.05s；OpenSpec strict validate passed；16/16 tasks 仍为 `all_done`；`py_compile` 与 `git diff --check` clean。测试和构建只有既有 warning。最终结论：0 CRITICAL、0 WARNING、0 SUGGESTION。

## Archive: 2026-08-23

- 三份 delta specs 已同步到主 specs：`agent-config` 追加 1 个 Requirement；新增 `code-agent-control-plane` 与 `code-agent-operator-ui` 主规范。
- `openspec validate --specs`：6 passed、0 failed；逐 capability 内容复核一致，无 delta 操作标记残留。
- change 已移动到 `openspec/changes/archive/2026-08-23-code-agent-control-plane-ui/`；16/16 tasks 完整，无归档 warning。
