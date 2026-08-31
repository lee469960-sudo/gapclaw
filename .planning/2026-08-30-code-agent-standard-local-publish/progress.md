# Progress: code-agent-standard-local-publish

## 2026-08-30

- 已创建 change，并完成 proposal、5 份规格、设计与任务清单。
- 尚未实施代码修改。

### Task 1.1

- **状态：** 已完成
- Manifest 编辑抽屉已移除 Local 发布配置区域、默认值、JSON 校验与保存 payload 字段。
- **验证：** `pytest -q tests/test_code_agent_control_plane_ui.py -k "manifest_component"` → **5 passed**。

### Task 1.2

- **状态：** 已完成
- Manifest API、响应序列化、草稿保存、校验与冻结契约已不再消费 local 发布字段；数据库列保留历史兼容，冻结契约暂时保持空 `local_publish`，等待平台标准档案注入。
- **验证：** `pytest -q tests/test_code_agent_control_plane_ui.py -k "manifest_component or manifest_api_does_not"` → **6 passed**。

### Task 2.1（进行中）

- 已开始新增平台管理的标准 local 发布档案解析；档案将冻结命令、凭据和镜像，且不读取历史 Manifest local 字段。
- **测试记录：** 首次命令在 `apps/api` 工作目录误用了 `apps/api/tests/...` 相对路径，pytest 未收集测试；将改用 `tests/...` 相对路径重跑，不把该路径错误当作代码失败。

### Task 2.1

- **状态：** 已完成
- 新增 `CODE_STANDARD_LOCAL_PUBLISH_PROFILES` 项目档案。该档案以项目 ID 绑定已注册的不可变命令、项目部署凭据和镜像 digest；网络、验证计划与锁继续由同一服务端命令注册表冻结。
- `freeze_task_contract` 仅在明确的 local 发布任务中注入平台档案；普通 CodeAgent 任务保持空发布契约。历史 Manifest local 字段不会参与解析。
- **验证：** `pytest -q tests/test_code_agent_local_publish.py tests/test_code_agent_control_plane_ui.py -k "standard_profile or manifest_component or manifest_api_does_not"` → **10 passed, 77 deselected**。

### Task 2.2

- **状态：** 已完成
- 新增受控扫描器：仅发现 Workspace 内实际存在的 Python `requirements.lock`/`requirements.txt`、Node `package-lock.json`（必须同目录有 `package.json`），同时记录 CI 和发布脚本位置供后续入口发现。
- 新增依赖准备器：仅使用平台镜像的 `python3`/`pip`/`npm`，以无 shell argv 在 `/tmp/code-agent-run-local` 中准备；不安装系统包、不写入 `/workspace`。
- **验证：** `pytest -q tests/test_code_agent_local_publish_dependencies.py tests/test_code_agent_local_publish.py -k "dependency or standard_profile"` → **6 passed, 16 deselected**；测试建立 Git 仓库并断言扫描后 `git status --porcelain` 仍为空。

### Task 2.3

- **状态：** 已完成
- 发布 runner Dockerfile 预装固定版本的 `dbt-core`、`dbt-clickhouse` 和固定系统工具 `curl`、`jq`、`yq`、`clickhouse-client`、Node/npm、Git；启动时统一探测这些标准工具。
- `build-code-agent-runner.sh --push` 保持 amd64/arm64 manifest-list 构建，并将 dbt 版本作为显式 build args；运行时若探测缺失，发布预检以 `local_publish_system_tools_missing` 阻塞确认。
- **验证：** `pytest -q tests/test_code_agent_runner.py tests/test_code_agent_local_publish.py -k "runner_records_fixed or runner_records_stable or runner_image or runner_multiarch or preflight_reports_missing or standard_profile"` → **8 passed, 39 deselected**。

### Task 3.1

- **状态：** 已完成
- local 发布运行阶段现在先扫描 Workspace、核对唯一发布脚本是否精确匹配平台冻结 argv，再准备锁定文件依赖。发现、准备和预检均发布脱敏中文事件，前端显示为独立步骤。
- 未找到入口或存在多个候选时，流程写入 blocked preflight 并在任何发布命令之前退出；依赖准备失败也不进入确认。
- **验证：** `python -m compileall -q app/services/code_agent/local_publish_dependencies.py app/services/agent_runtime/runtime.py` 通过；`pytest -q tests/test_code_agent_local_publish_dependencies.py tests/test_code_agent_control_plane_ui.py -k "dependency or entry_discovery or conversation_labels_standard"` → **4 passed, 67 deselected**。

### Task 3.2

- **状态：** 已完成
- 确认后路径继续只通过冻结 argv 执行一次，结果先脱敏保存，再进入 Verifier 与资源清理。未知、失败或缺少发布证据均为失败/不确定终态，且没有自动重试分支。
- **验证：** `pytest -q tests/test_code_agent_local_publish.py tests/test_code_agent_verifier.py -k "execute_local_publish_uses_frozen or marks_runner_timeout or verifier_requires_local_publish or verifier_never_treats"` → **4 passed, 35 deselected**。

### Task 4.1

- **状态：** 已完成
- Manifest 抽屉增加说明：标准 local 发布由平台档案管理，用户不再填写命令、凭据、网络或依赖。`.env.example` 明确项目档案格式、固定镜像工具和 run-local 锁定文件依赖边界；runner 镜像头部说明同步更新。
- **验证：** `pytest -q tests/test_code_agent_control_plane_ui.py -k "manifest_component_hides_local_publish_fields or conversation_labels_standard"` → **2 passed, 66 deselected**。

### Task 4.2

- **状态：** 已完成
- 后端回归：`pytest -q tests/test_code_agent_local_publish.py tests/test_code_agent_local_publish_dependencies.py tests/test_code_agent_runner.py tests/test_code_agent_verifier.py tests/test_code_agent_control_plane_ui.py` → **137 passed**。
- 前端：`npm run build` → **通过**（Vite 仅报告既有的大 chunk 警告）。
- 质量门禁：`git diff --check` → **通过**；`openspec validate code-agent-standard-local-publish --strict` → **通过**。
- 发布任务识别已收紧：只有请求同时明确包含 local/本地及发布、部署或 release 动作词时，才会创建平台发布契约；仅检查/修复 CI 不会触发发布。
