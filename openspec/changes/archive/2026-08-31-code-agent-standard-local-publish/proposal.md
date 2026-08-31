## Status

**OBSOLETE.** 已被 `code-agent-minimal-privilege-local-publish` 取代。当前运行代码不再使用平台标准发布档案、命令注册表或一次确认门禁。归档时 `skip_specs: true`，不把本 change 的平台档案/确认门禁合进主 spec。

## Why

Manifest 中的 local 发布命令、依赖、网络、Secret、验证计划和锁让项目配置难以理解，而且这些值与服务端命令注册表重复，容易产生不一致。标准 local 发布应当是一条受控工作流：用户提出任务、系统准备仓库工具链、用户确认一次、平台执行并验证。

## What Changes

- **BREAKING** 移除 Manifest 编辑界面的全部 local 发布字段；历史字段不再作为新 Run 的发布输入。
- 增加平台管理的标准 local 发布档案，固定执行边界和项目级凭据绑定，不由 Manifest 逐项覆盖。
- Claude Code 扫描仓库的 CI 配置、发布脚本和锁定文件，生成可审计的应用依赖准备计划；平台只按声明的锁定文件安装应用依赖。
- 发布 runner 镜像预装固定版本的系统工具（dbt、jq、yq、ClickHouse client 等）；不允许模型任意下载系统二进制。
- 对话展示发现的发布命令、依赖准备结果和一次确认门禁；确认后执行一次 local 发布并输出验证结果。

## Capabilities

### New Capabilities

- `code-agent-standard-local-publish`: 平台默认 local 发布档案、仓库依赖准备和一次确认执行闭环。

### Modified Capabilities

- `code-agent-control-plane`: Manifest 不再承载 local 发布参数，项目 readiness 改为评估平台默认发布档案。
- `code-agent-conversation-progress`: 对话展示发布发现、依赖准备、确认和验证过程。
- `code-agent-sandbox-runtime`: 发布 runner 使用预装系统工具，并只允许按仓库锁定文件准备应用依赖。
- `code-agent-operator-ui`: 移除 Manifest Local 发布配置区域及其字段校验。

## Impact

- Manifest API、模型和控制面校验不再消费 local 发布字段；历史数据库列仅保留兼容，不作为新 Run 契约。
- Local 发布 Runtime、runner 依赖准备、可信镜像构建及对话事件需要调整。
- Code Project/Manifest 编辑页面简化，发布凭据和标准档案移至平台受控配置。
