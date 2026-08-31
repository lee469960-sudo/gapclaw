## ADDED Requirements

### Requirement: Manifest UI 必须只配置 Git 基线

Manifest UI SHALL 只展示并编辑来源类型、仓库地址、Git 凭据和请求 Ref。UI MUST NOT 展示解析快照状态、源码扫描状态、运行镜像、工具、允许路径、验证计划、预算、策略、local 发布或依赖配置；这些资源由 CodeAgent 编辑页现有资源配置和绑定 Sandbox 承担。

#### Scenario: 管理者编辑 Manifest
- **WHEN** 管理者打开 Manifest 页面
- **THEN** 页面仅显示 Git 连接和版本基线字段
- **AND** 页面说明运行资源在 CodeAgent 编辑页配置

#### Scenario: 发布 Git 基线
- **WHEN** 管理者保存并发布有效 Git 配置
- **THEN** UI 显示 Manifest 已发布和请求 Ref
- **AND** 不要求填写 Sandbox、模型、Skill、MCP、镜像或发布字段
