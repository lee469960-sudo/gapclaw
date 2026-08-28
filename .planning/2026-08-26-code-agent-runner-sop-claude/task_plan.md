# Task Plan: code-agent-runner-sop-claude（Execution Map）

## Goal

让 `claude_code` 在可信 runner 里按固定 SOP 真正跑起来：镜像含 Claude Code + OpenSpec CLI + SOP 技能；开箱前 grill；`开始实现` 后起沙箱；Agent 绑定 LLM 注入凭证；平台 Verifier/Sealer 为门；仅 seal 成功后 archive。

## Next Step

Phase 8 complete. Configure `dbt-test` with a single Cloud Claude-compatible LLMResource, restart API, then re-run `开始实现`. Archive only if the user asks (`/opsx:archive`). Do not publish `dbt_test` as `claude_code`.

## Current Phase

Phase 8 complete (Claude Code model binding closure)

## Phases

### Phase 1: OpenSpec artifacts

- **OpenSpec task mapping:** proposal, specs, design, tasks
- **Depends on:** grilled consensus
- **Status:** complete

### Phase 2: Image + SOP assets

- **OpenSpec task mapping:** 1.1–1.2
- **Depends on:** Phase 1
- **Status:** complete

### Phase 3: Runtime (network, credentials, SOP, grill, archive)

- **OpenSpec task mapping:** 2.1–5.1
- **Depends on:** Phase 2
- **Status:** complete

### Phase 4: Tests

- **OpenSpec task mapping:** 6.1
- **Depends on:** Phase 3
- **Status:** complete

### Phase 5: Image rebuild + digest (no dbt_test publish)

- **OpenSpec task mapping:** operator digest
- **Depends on:** Phase 4
- **Status:** complete

### Phase 6: Claude Code operational closure

- **OpenSpec task mapping:** 7.1–7.4
- **Depends on:** Phase 5
- **Status:** complete
- **Execution map:**
  - 7.1: make `/workspace/.git/` the normal Git metadata for Claude Code and keep `source.git` only as a compatibility pointer to the same metadata.
  - 7.2: reject `git commit` through shell policy and fail integrity on HEAD/ref drift.
  - 7.3: remove retired pre-Claude-Code digest from trusted config and delete old `code-agent-*` containers using it, preserving host workspaces.
  - 7.4: add multi-arch buildx guidance; actual push requires a target registry.

### Phase 8: Claude Code model binding closure

- **OpenSpec task mapping:** 8.1–8.3
- **Depends on:** Phase 6
- **Status:** complete
- **Execution map:**
  - 8.1: split `model_unavailable` reasons in runtime env resolution.
  - 8.2: reject `claude_code` Code Agent save when LLM is group or lacks key/model.
  - 8.3: surface specific reason in failure/result copy.

## Boundaries

- 新 OpenSpec change `code-agent-runner-sop-claude`。
- 不自动 publish `dbt_test`。
- 不开出站代理；`claude_code` 用 `bridge`（全出网）。
- grill-me 不进 runner 镜像。
- feature flag 默认 false。

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Agent 绑定 LLM 作为 Claude Code 凭证/模型 | 用户覆盖独立 CODE_CLAUDE_API_KEY |
| 确认口令「开始实现」 | 可测，不误开 Docker |
| SOP 技能自动注入 | 选了 claude_code 必须有 /opsx:* |
| archive 仅 seal 后 | 失败 patch 不得归档 |
| `/workspace` 必须是正常 Git repo | Claude Code 需要原生 `git status/diff/log` |
| 旧 runner digest 清理 | 新 run 不得再落到无 Claude Code 的 runner |
| 生产使用 multi-arch manifest-list digest | 同一 trusted image 覆盖 arm64/amd64 |
| `claude_code` 禁止隐式解析 LLM group | 保证 coding run 模型/凭证可复现、可审计 |

## Errors Encountered

| Error | Attempt | Resolution |
|-------|---------|------------|
| FakeRunner.exec rejected environment= | 1 | Optional kwarg on fakes |
| Frozen model_config empty base_url | 1 | Only set when non-empty |
| Official Debian apt 500/502 on arm64 | 1 | Rebuild with Aliyun `DEBIAN_MIRROR` |
| npmjs hung installing Claude Code | 1 | Added optional `NPM_REGISTRY` (npmmirror locally) |
| planning catchup script missing at `/Users/lizhidong/.codex/...` | 1 | Continue from active `.planning` files injected by session |
| Full Dockerfile rebuild stalled on apt package index | 2 | Local refresh build layered updated helper/SOP/ENV onto existing runner image; production multi-arch build remains scripted |
| `开始实现` failed with `model_unavailable` | 1 | `dbt-test` uses LLM group `36bee1c3`; add single-LLM validation and specific reason mapping |

## Per-Task Completion Gate

1. 完成该 task 对应代码或交付物。
2. 运行该 task 声明的测试或可观察验证。
3. 在 `progress.md` 写入实际变更、验证命令和结果。
4. 勾选 `tasks.md`。
