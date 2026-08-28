## Why

`code-agent-runner` 已能预装 Claude Code CLI，但本地仍跑旧 digest；容器默认 `network_mode=none` 且不注入 Agent LLM 凭证，因此 `claude_code` 无法真正编码。Operator 需要在沙箱内按固定 SOP（propose → planning-with-files → apply → 实现 → verify）出 patch，grill 在开箱前完成，平台 Verifier/Sealer 仍是唯一成功门。

## What Changes

- 重建可信 runner 镜像：保留 pinned Claude Code；预装 pinned OpenSpec CLI；烘焙 OpenSpec propose/apply/verify/archive 与 planning-with-files 技能（含 scripts）。**不**烘焙 grill-me。
- 仅当冻结 `coding_runtime=claude_code` 时，runner 使用 Docker `bridge`（全出网，**不是**域名 allowlist）。legacy 仍 `network_mode=none`。
- Claude Code exec 从 Agent 绑定的 LLMResource 注入 `api_key` / `base_url` / `model`；缺密钥 fail closed。密钥不得写入 transcript、`runner_facts` 或 `CLAUDE.md`。
- `claude_code` 初始化自动落地 SOP 技能与 run-local `CLAUDE.md`：同一 run 授权走完 propose→planning→apply→实现→`/opsx:verify`，**禁止** session 内 archive。
- `claude_code` 对话两阶段：开箱前用外层 Agent LLM grill；用户原样回复 `开始实现` 才 `create_code_run`。legacy 行为不变。
- OpenSpec `archive` 仅在 Sealer 成功之后由控制面执行。已发布 Manifest（含 `dbt_test`）不自动改 `coding_runtime`。`code_claude_code_runtime_enabled` 默认仍 false。

## Capabilities

### New Capabilities

- `code-agent-coding-sop`: CodeAgent `claude_code` 的默认编码 SOP（技能注入、`CLAUDE.md`、开箱前 grill、seal 后 archive）。

### Modified Capabilities

- `code-agent-sandbox-runtime`: `claude_code` 允许 `bridge`；镜像必须含 OpenSpec CLI 与 SOP 技能；legacy 仍无网络。
- `code-agent-profile`: `claude_code` 在创建 run / 启动沙箱前必须完成 grill 确认口令；legacy 仍一条消息即建 run。
- `code-agent-verification`: OpenSpec change 的 archive 不得早于平台 Sealer 成功。

## Impact

- 镜像：`deploy/code-agent-runner.Dockerfile`；SOP 资产与 API 共用一份源（API 物化到 workspace）。
- 后端：`RunnerSpec` / `CodeContainerRunner` 网络与 exec env；`claude_code_runtime` 模型映射与 SOP 物化；`agent_chat` 两阶段入口；Sealer 成功后 `openspec archive`。
- 测试：协议、runner spec、chat 入口、技能物化、凭证脱敏、archive 时序。
- 部署：重建镜像后更新 `CODE_TRUSTED_IMAGE_DIGESTS`；本地 `.env` 打开 feature flag。不 publish 现有项目。
- 非目标：出站代理 / 域名 allowlist、独立 Claude 密钥平面、grill UI 按钮、自动把 `dbt_test` 改为 `claude_code`。
