## Why

当前 Claude Code CodeAgent 已能创建 run、物化 workspace 并加载 Skill，但实际任务仍可能卡在模型绑定、repo root 语义和目标定位阶段：`dbt-test` 最新 run 在 model preflight 因 LLM Group 失败，workspace 里业务文件落在 `gamestat/` 子目录且 `/workspace` 不是正常业务 Git worktree，用户看到的“仓库未挂载”也无法区分真实原因。

本 change 目标是让 Claude Code CodeAgent 在启动前给出可修复的配置引导，在启动后拥有稳定的 `/workspace` repo root，并在简单配置替换任务中能明确进入 coding loop 或以可理解的非成功终态退出。

## What Changes

- 支持 `开始执行:<objective>` 作为 Code Profile + `claude_code` 的显式执行入口；冒号后内容作为 objective，原始文本进入审计，空 objective 不创建 run。
- 保留 `开始实现` / `开始执行` 单独确认口令；`开始执行:<objective>` 跳过 grill 并直接创建 run。
- 为 Claude Code Agent 的 LLM Group / 缺 key / 缺 model 情况提供 UI/API 修复引导；不自动创建 LLMResource，不自动从 Group 选择成员，不自动改绑 `dbt-test`。
- 新 run 将单一顶层 repo 内容平铺为 `/workspace` repo root；仅当 snapshot 根下除 `.git` / runtime ignored 文件外只有一个普通目录时才剥离该目录。
- `/workspace/.git` 来自 sanitized snapshot，历史 run 不迁移，run 阶段不重新 clone。
- 新增公开非成功终态 `target_not_found` 和 `needs_user_decision`：
  - 找不到明确目标时返回 `target_not_found`，不生成 patch。
  - 多个 local/profile/dev 候选或范围不清时返回 `needs_user_decision`，保留不可执行 workspace 供用户查看候选。
- UI 状态拆分 workspace 文件、Git metadata、repo root、container mount 和 model preflight，避免把 repo files missing、git metadata missing、repo root mismatch 混成“仓库未挂载”。
- 对 IP 替换类任务，平台层只要求能正确启动、加载 Skill、进入 coding loop，并在旧目标不存在时明确 `target_not_found`；不要求 CI 真实调用 Claude。
- `host-validate.sh` 只作为最终用户可复制的文本块输出，不写入 repo，也不作为自动 Verifier 门禁。

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `code-agent-profile`: 增加 `开始执行:<objective>` 入口语义，并保持只影响 Code Profile + `claude_code`。
- `code-agent-coding-runtime`: 增加 `target_not_found`、`needs_user_decision` 的 runtime 结果语义，并要求目标定位失败/多候选时不伪装成基础设施或验证失败。
- `code-agent-workspace`: 调整新 run 的 repo root 物化语义，使单顶层 snapshot 可安全平铺到 `/workspace`，历史 run 不迁移。
- `code-agent-verification`: 明确 `target_not_found` 不生成 patch，`needs_user_decision` 不产生可采用 patch，host 验证命令仅作为人工辅助输出。
- `code-agent-operator-ui`: 增加模型绑定修复引导和细分 readiness 状态展示。

## Impact

- 后端：CodeAgent chat gate、run objective 构造、workspace 物化、sanitized `.git` 放置、runtime 结果枚举、failure/result presentation、readiness facts。
- 前端：Agent 编辑页/CodeAgent 结果页的 LLM 修复引导、readiness 状态拆分、`target_not_found` / `needs_user_decision` 展示。
- 测试：后端单元/集成使用 mock Claude Code；真实 `dbt-test` run 作为本地验收步骤，前置条件是用户在 UI 绑定单个可用 LLMResource。
- 非目标：不改 dbt Skill，不自动配置凭证，不迁移历史 run，不让 host validation 成为平台门禁。
