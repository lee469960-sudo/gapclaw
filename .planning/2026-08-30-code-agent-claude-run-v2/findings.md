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

- 当前尚未执行 implementation，未发现阻断性问题。
