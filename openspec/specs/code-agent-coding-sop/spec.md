# code-agent-coding-sop Specification

## Purpose

定义 CodeAgent 在 `coding_runtime=claude_code` 时的默认编码 SOP：开箱前 grill、沙箱内 OpenSpec 与 planning-with-files、平台 Verifier/Sealer 为独立验证、仅封存成功后 archive。

## Requirements

### Requirement: claude_code 必须自动注入 SOP 技能与 run-local 工作说明

当冻结契约为 `coding_runtime=claude_code` 时，系统 SHALL 在 Claude Code 启动前将 propose、apply、verify、archive 与 planning-with-files 技能写入该 run Workspace 的 Claude Code 技能目录，并写入 run-local `CLAUDE.md`。`CLAUDE.md` SHALL 授权同一 run 走完 propose → planning-with-files → apply → 实现 → OpenSpec verify，MUST NOT 等待第二次人类授权，MUST NOT 在 coding session 内 archive，MUST NOT 要求容器内 grill。未选择 `claude_code` 时 MUST NOT 注入这些 SOP 文件。

#### Scenario: 选择 Claude Code 后技能与 SOP 就绪

- **WHEN** `claude_code` run 完成 Workspace 准备
- **THEN** Workspace 含上述五套技能文件与 `CLAUDE.md`
- **AND** 不包含 grill-me 技能

#### Scenario: legacy 不注入 SOP

- **WHEN** run 使用 `coding_runtime=legacy`
- **THEN** 系统不因本能力写入 SOP 技能或 `CLAUDE.md`

### Requirement: Claude Code 必须使用 Agent 绑定 LLM 的配置

`claude_code` 启动时，系统 SHALL 使用该 Agent 绑定 LLMResource 的密钥、base URL 与模型名配置 Claude Code。密钥 SHALL 仅通过 runner exec 环境注入。系统 MUST NOT 把密钥写入 `CLAUDE.md`、技能文件、`runner_facts`、事件或用户可见 transcript。绑定缺失或密钥为空时 SHALL 以 `model_unavailable` fail closed。

#### Scenario: 绑定 LLM 可用于 Claude Code

- **WHEN** Agent 绑定了带非空密钥的 LLMResource 且 run 为 `claude_code`
- **THEN** Claude Code 使用该资源的模型名与 base URL
- **AND** 密钥只出现在 exec 环境中

#### Scenario: 缺少绑定或密钥

- **WHEN** Agent 未绑定 LLM 或解密后密钥为空
- **THEN** 系统不得启动 Claude Code coding loop
- **AND** 失败分类为 `model_unavailable`

#### Scenario: 审计路径不含明文密钥

- **WHEN** Claude Code 已启动或失败
- **THEN** `runner_facts`、事件、`CLAUDE.md` 与脱敏 transcript 不含 API 密钥明文
