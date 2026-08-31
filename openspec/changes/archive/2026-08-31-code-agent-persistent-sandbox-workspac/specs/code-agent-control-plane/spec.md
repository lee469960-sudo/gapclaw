## MODIFIED Requirements

### Requirement: Manifest 以草稿和已发布版本管理

系统 SHALL 允许项目管理者为 Code Project 创建和编辑草稿 Manifest，并在仓库来源、凭据引用和请求 Ref 字段校验通过后发布版本。Manifest SHALL 仅表达受管仓库来源类型与标识、可选 Git Secret 引用和请求的 branch/tag/commit；发布时 MUST NOT 导入仓库、扫描源码或封存源码快照。历史已发布版本 SHALL 可查看但不可被修改，且 MUST NOT 保存秘密值。运行 Sandbox、模型、Skill、MCP、策略、路径、验证计划、镜像和预算 MUST NOT 由 Manifest 配置。

#### Scenario: 发布有效 Manifest
- **WHEN** 管理者提交来源、凭据引用和请求 ref 字段有效的草稿
- **THEN** 系统发布仅包含 Git 配置的新版本
- **AND** 后续任务从 CodeAgent 编辑页资源配置获取运行环境
- **AND** 后续任务在绑定 Sandbox Workspace 内解析实际 commit

#### Scenario: 保存未完成草稿
- **WHEN** 管理者保存缺少 Git 必填信息的草稿
- **THEN** 系统保存草稿并标出 Git 字段问题
- **AND** 该草稿不得用于新任务

#### Scenario: 发布缺少 Git 信息的 Manifest 被拒绝
- **WHEN** 管理者尝试发布缺少仓库、ref 或可用凭据的草稿
- **THEN** 系统拒绝发布并返回字段级原因
- **AND** 不要求填写镜像、预算、验证计划或发布配置

#### Scenario: 发布无效 Manifest 被拒绝
- **WHEN** 仓库来源、凭据引用或 Ref 字段无法校验
- **THEN** 系统拒绝发布并保留当前已发布版本
- **AND** 返回可行动的 Git 字段原因
