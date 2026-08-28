## Why

CodeAgent 初始化在「准备 Code Workspace」成功后，于「启动 Code Sandbox」阶段以稳定原因 `workspace_mount_invalid` 失败。根因是 Workspace 落盘根目录与 mount 校验所用的配置根不一致：`WorkspaceManager` 默认写入 `{data_dir}/code_agent/runs`，而 `CODE_WORKSPACE_API_ROOT` / host-root 映射与 compose 约定使用 `{data_dir}/code-agent/runs`。prepare 成功、bind 映射 fail-closed，用户无法进入可执行 Sandbox。

## What Changes

- 让 Workspace 物化根目录与配置的 Workspace API root（及空配置时的规范 fallback）对齐，消除 `code_agent` vs `code-agent` 分叉。
- 明确：Sandbox bind 映射所校验的 API 可见路径，必须就是 prepare 写入的同一权威根下的 `<run_id>/workspace`。
- 清理开发环境中因错误默认根产生的孤儿 `data/code_agent/runs` 目录（非运行时行为，属运维收尾）。
- 补充回归测试，防止默认根再次漂移。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `code-agent-workspace`: 要求每 run Workspace 物化到配置的 Workspace API root（或规范 fallback），不得使用与 mount 配置无关的隐式目录名。
- `code-agent-sandbox-runtime`: 澄清 bind-path 映射失败（含根不一致导致的路径逃逸/不在 API root 内）必须以 `workspace_mount_invalid` fail closed，且与 prepare 共用同一权威 API root。

## Impact

- 代码：`apps/api/app/services/code_agent/workspace.py`（`WorkspaceManager` 默认根）；相关单测 / mount / runner 回归。
- 配置：继续以 `.env` / compose 中的 `CODE_WORKSPACE_API_ROOT` 与 `CODE_WORKSPACE_HOST_ROOT` 为权威；空 API root 时 fallback 为 `{data_dir}/code-agent/runs`。
- 部署：不改 compose 契约；本地需重启 API 后验证 CodeAgent 初始化可过 Sandbox 启动。
- 数据：可删除孤儿 `apps/api/data/code_agent/runs`（开发垃圾，不迁移）。
