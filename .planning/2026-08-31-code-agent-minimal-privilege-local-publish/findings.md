# Findings: code-agent-minimal-privilege-local-publish

## 2026-08-31 — 初始化

- OpenSpec schema 为 `spec-driven`，8 项任务均未完成。
- 现有工作区包含上一轮 `code-agent-standard-local-publish` 的未提交实现：`CODE_STANDARD_LOCAL_PUBLISH_PROFILES`、`CODE_LOCAL_PUBLISH_COMMANDS`、平台档案解析、确认门禁及 UI 提示仍在代码中。
- 现有 local 发布相关模型/数据库字段来自更早的 Manifest 实现；当前变更要求保持 Manifest 不含 local 发布字段，因此仅移除本轮新增的配置/运行依赖，避免无关 schema 重构。
- runner 当前已强制普通用户、cap-drop、no-new-privileges；需新增仅供平台内部调用的 root 安装入口，且实际发布与 Coding Loop 不能使用它。

## 2026-08-31 — 执行记录

- 一次聚焦测试因工作目录已切换至 `apps/api` 却仍传入 `apps/api/...` 相对路径而未启动；下一次使用 `tests/...` 路径，未重复该命令。
- 一次引用搜索同样误用了 `apps/api/...` 前缀；`python -m compileall -q app` 仍成功完成。后续在该工作目录统一使用 `app/`、`tests/`。
