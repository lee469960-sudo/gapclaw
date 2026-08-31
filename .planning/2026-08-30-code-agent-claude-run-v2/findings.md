# Findings: code-agent-claude-run-v2

## Initial discoveries

- OpenSpec change 使用 `spec-driven` schema，proposal、design、6 份 spec delta 与 tasks 已完整生成。
- `tasks.md` 共 20 条任务，按控制面、Sandbox/工具链、执行/证据、Verifier/UI、集成验收五组组织。
- 现有系统已有 Manifest、项目策略、Sandbox runner、Verifier、对话事件流和审计能力；本 change 设计为增量扩展。

## Decisions already confirmed

- 仅允许 local 环境；在现有 Sandbox 内执行；只使用 Manifest 登记的 canonical 命令。
- 默认禁止 `git add/commit/push`、任意 Shell、提权、Docker Socket 和宿主机路径。
- 采用最小网络 allowlist、运行时 Secret 注入、固定 digest 的 amd64/arm64 镜像和非自动重试策略。
- 发布前必须 preflight/dry-run 和一次人工确认；成功需退出码、发布证据、local 状态验证全部通过。

## Facts to verify during implementation

- 目标仓库/CI 中 canonical local 发布命令的准确路径、参数和实际工具依赖。
- 发布完成后可稳定查询的发布 ID、状态接口或验证脚本。

## Open issues

- Implementation 已完成；完整仓库测试命令包含既有长时间数据生成用例，详见 Phase 5 findings。变更相关回归未发现阻断性问题。

## Task 2.1 findings

- 现有 runner 已有双架构 buildx 脚本与 digest 固定启动逻辑；本任务补充固定 dbt/curl 依赖和启动时工具预检，未新增 Sandbox 或镜像发布平面。
- 依赖预检只接受注册表中的有限工具名，未知依赖在容器启动后立即 fail closed。
- 本地验证首次使用错误工作目录/测试路径导致 FileNotFoundError，已改用 `apps/api` 目录的正确命令并通过；该错误不影响实现。
- 2026-08-30 用户明确运行环境不必备 dbt、jq、clickhouse-client；当前决策覆盖上面的早期实现假设：基础 runner 不预装这些业务工具，依赖探测仅作为审计，不得导致 Workspace/Runtime `startup_failed`。
- 最新 Run `c0d1925b` 已能启动并保留 Workspace，但 Claude 摘要仍把 `dbt is not installed` 作为前置主线，并准备查找替代 dbt 环境。原因是无网络提示只要求“缺工具就报告”，没有说明 dbt/jq/clickhouse-client 不是 sandbox startup prerequisite。已改为仅当具体仓库命令直接需要该工具时才视为阻塞。
- 最新 Run `03938ff8` 已达到 `patch_ready`，但 Claude 在编码循环内直接执行了 `cd /workspace/gamestat && bash scripts/deploy.sh local ods models/schema_ods.yml dbt_ods_sql`，仓库脚本输出 `Required command not found: dbt`。这不是 runner 启动门禁，而是 Claude 越过了“local publish 必须由平台受控阶段执行”的边界。已在 runtime prompt 明确禁止 Claude Code coding loop 执行 deploy/publish/release/CI 发布命令，只允许读取脚本定位 canonical command。

## Task 2.2–2.3 findings

- 现有 inspect 校验已强制单一 Workspace 挂载，因此 Docker Socket 和宿主机路径不能通过额外挂载进入容器；普通 CodeAgent 的 `none` 网络行为保持不变。
- Docker bridge 本身不提供域名级 egress allowlist；实现将授权目的地冻结进 RunnerSpec，并在配置/preflight 做严格 URL/host/port 校验，后续执行器必须继续使用该冻结列表，不得自行扩展。
- 新增测试首次因 mock inspect facts 仍为 `none` 而触发 `runner_facts_mismatch`，已让测试按 local contract 生成 bridge facts 后通过。

- Secret 注入复用现有 importer identity 与 lease 生命周期；发布执行器必须通过 context manager 使用环境映射，禁止直接读取 `secret_enc`。

## Phase 3 findings

- local publish 在 Runtime 中作为已有 CodeAgent run 的阶段执行；未确认的 Run 不会调用 runner，状态未知不进入自动 retry。
- 发布输出先按实际 Secret 值替换为 `[REDACTED]`，再经过通用输出扫描；release ID 只接受受限字符格式。
- 普通 CodeAgent 与 Claude Code 的既有 verifier/sealer 流程保持兼容，local 事件复用相同历史存储。

## Phase 5 findings

- 试点登记仅提供脱敏模板，不写入真实凭证；部署者需将审核后的 JSON 与批准 digest/Secret 绑定到 Manifest。
- 完整 `pytest -q tests/test_code_agent_*.py` 在 544 passed、4 skipped 后被仓库既有长时间数据生成测试中断；重点变更回归与编译/构建/diff 检查均通过。
- `CODE_LOCAL_PUBLISH_ENABLED` 默认 false，只有显式开启并配置命令注册表才会进入 local publish 流程；普通 CodeAgent 不受影响。

## Post-verification local publish handoff finding

- 用户任务“使用 CI 中的发布命令执行 local 环境发布”暴露出两处缺口：一是 publish-only 任务仍进入 Claude Code coding loop，导致 Claude 正确拒绝直接执行副作用命令但无法完成平台发布；二是 `confirm_local_publish` 只更新确认状态和锁，没有调度平台受控 `execute_local_publish`。
- `CodeAgentRun` ORM 缺少 `publish_state`、`publish_preflight`、`publish_confirmation`、`publish_result` 映射，导致新库/测试路径无法可靠持久化 Run 级发布状态；旧 SQLite 迁移也需要补齐 `code_agent_runs`，同时保留 `code_scan_reports` 兼容列。
- 正确闭环：publish-only 任务跳过 Claude 模型预检与 coding loop，保留 active Workspace/Runner 等待人工确认；用户在同一对话回复“确认”时复用原 Run，调度后台 local publish stage，发布完成后再执行 Verifier、Sealer、cleanup 和 Markdown 结果输出。
- Manifest API 已有 local publish 字段，但 `CodeProjects.vue` 没有暴露 `local_publish_dependencies` 等高级配置，导致用户只能通过 API/数据库配置。已补 Manifest 抽屉中的 Local 发布配置区域，字段直接沿用后端字段名。
