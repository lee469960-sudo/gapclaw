# Findings & Decisions

## Requirements

- 镜像默认安装 Claude Code（Dockerfile 已 pin 2.1.246；需重建 digest）。
- 同时安装 OpenSpec CLI + propose/apply/verify/archive + planning-with-files。
- 不安装 grill-me 到 runner。
- CodeAgent 初始化默认 SOP：grill（开箱前）→ propose → planning-with-files → apply → 实现 → opsx:verify → 平台 Verifier/Sealer → archive。
- `coding_runtime=claude_code` 才启用；已发布项目不自动切换。

## Research Findings

- 当前本地 trusted digest：`code-agent-runner@sha256:5f2b40575e43f22cb66cf724404854aefbda15f81915dc5f99824d219ff195e6`（Claude 2.1.246 + OpenSpec 1.10.0 + SOP + `/workspace/.git` helper/env fix）。旧 `aed8f761…` 已被用户确认为 retired 并已从 trusted config 移除；中间 digest `43d804…` 被本轮刷新层 supersede。
- `RunnerSpec.from_dict` 硬拒绝非 `none` 网络；`spec_for_run` 写死 `network_mode=none`。
- `runner.exec` 不注入 `ANTHROPIC_*`；测试断言命令不含模型密钥。
- `model_config.model_ref` 当前是 `agent.llm_id`（平台 ID），不是 LLMResource.model。
- `submit_chat` 对 code profile 立即 `create_code_run`；`profile==code` 且无 `code_run_id` 会 `code_run_required`。
- OpenSpec propose 技能默认「规划完就停」；沙箱 SOP 必须用 CLAUDE.md 覆盖。
- planning-with-files 官方 SKILL hook 指向宿主机 `$HOME`；容器内需改路径或去掉 hook、把规划写进 CLAUDE.md。
- 本机 OpenSpec CLI：`@fission-ai/openspec@1.10.0`。

## Technical Decisions

| Decision | Rationale |
|----------|-----------|
| SOP 源在 API 包内并 COPY 进 runner 镜像 | API 在 host 物化 workspace；镜像内 Claude 也能读到同一套文件 |
| exec env 注入密钥，不写文件 | 满足「配置文件不得明文秘密」 |
| grill 走无 code_run 的 code profile 分支 | 不能起 Docker；仍用 Agent 绑定 LLM |

## Issues Encountered

| Issue | Resolution |
|-------|------------|
| deb.debian.org 500/502 | `--build-arg DEBIAN_MIRROR=http://mirrors.aliyun.com/debian` |
| npmjs 安装 Claude Code 卡住 | `ARG NPM_REGISTRY` + npmmirror |
| 只换新 digest 会让旧 Manifest 失就绪 | 最新决策：不改写历史 Manifest；旧 Manifest 显示不可运行，需重新发布 |
| Verify found archive result not persisted | Store sanitized result in `runner_facts.claude_code_openspec_archive` after successful Sealer archive hook |
| Verify found missing dedicated no-archive/legacy SOP tests | Added regression coverage for verifier failure, sealer failure and legacy no SOP materialization |
| Verify requested bridge comment clarity | Runtime comment now states Docker `bridge` is full egress, not a domain allowlist |
| Live old runner missing Claude Code | Container `code-agent-1fbadf24` uses old digest `aed8f761…`; cleanup scope is old-digest `code-agent-*` containers only |
| `/workspace` is not a Git repo | Current materialization puts sanitized Git metadata in `run_root/source.git`, outside the mounted `/workspace`; fix requires real `/workspace/.git/` |
| planning catchup script missing | `/Users/lizhidong/.codex/skills/planning-with-files/scripts/session-catchup.py` not present; continue from active `.planning` files |
| Full local Dockerfile rebuild hit repeated apt index stalls | Interrupted after repeated `trixie/main arm64 Packages` retries; refreshed from existing `code-agent-runner:1` layer to avoid apt/npm and generated digest `5f2b4057…` |

## 2026-08-27 Operational Closure Decisions

| Decision | Rationale |
|----------|-----------|
| Claude Code belongs in trusted runner image only | API service image should not need coding CLI |
| Retire old runner digest and delete matching old `code-agent-*` containers | New runs must not land on a runner without Claude Code |
| `/workspace` must contain real `.git/` | Claude Code expects native `git -C /workspace status/diff/log` |
| Sanitized `.git` may be writable run-local metadata | It is copied from sealed snapshot, has no remotes/hooks/alternates/credentials |
| Production runner digest should be registry manifest-list `image@sha256` | One trusted reference covers arm64 and amd64 |
| Claude Code runtime must not resolve LLM groups implicitly | Reproducible coding runs need a stable single model/key; group fallback makes audit and failure diagnosis ambiguous |

## 2026-08-28 Model Binding Findings

- `dbt-test` Agent (`8c862281`) is `profile=code` and bound to project `ae834a47`.
- Latest published Manifest for project `ae834a47` is v6 with `coding_runtime=claude_code` and trusted digest `5f2b4057…`.
- `dbt-test.llm_id` points to LLMResource `36bee1c3`, `type=group`, name `M3 AND V4`.
- That group has no direct `api_key_enc` and no direct `model`; members include MiniMax, DeepSeek, and local Qwen.
- Current `resolve_claude_code_exec_env` expects a direct LLMResource key/model, so the group binding causes `model_unavailable`.
- Decision: MVP rejects LLM group for `claude_code`; use a single key/model LLMResource. Provider/base_url are not hard-gated until an explicit Anthropic-compatible gateway contract exists.

## Resources

- `deploy/code-agent-runner.Dockerfile`
- `apps/api/app/services/code_agent/runner.py`
- `apps/api/app/services/code_agent/claude_code_runtime.py`
- `apps/api/app/routers/agent_chat.py`
- OpenSpec change: `openspec/changes/code-agent-runner-sop-claude/`
