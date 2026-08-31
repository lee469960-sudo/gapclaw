## MODIFIED Requirements

### Requirement: Manifest 以草稿和已发布版本管理

系统 SHALL 允许项目管理者为 Code Project 创建和编辑草稿 Manifest，并在完整性与安全策略校验通过后发布不可变版本。Manifest SHALL 至少表达受管仓库来源类型与标识、可选 Secret 引用、请求的 branch/tag/commit、发布时解析的精确 commit SHA 与源码快照、允许路径、验证计划、按 digest 固定的可信镜像、允许工具和资源预算；历史已发布版本 SHALL 可查看但不可被修改，且 MUST NOT 保存秘密值。Manifest MUST NOT 表达 local 发布命令、依赖、网络、发布 Secret、验证计划或锁；这些发布配置由平台标准 local 发布档案管理。

#### Scenario: 保存未完成草稿

- **WHEN** 管理者保存尚未具备全部必填约束的 Manifest
- **THEN** 系统保存为草稿并标出缺失、无效或未经批准的字段
- **AND** 该草稿不得用于创建 Code run

#### Scenario: 发布有效 Manifest

- **WHEN** 管理者提交包含全部必填约束、来源与凭据引用可用、ref 可解析且通过策略校验的草稿
- **THEN** 系统发布一个冻结精确 commit SHA、源码快照标识、镜像 digest 和有效策略的新版本化 Manifest
- **AND** 后续 Code run 使用该已发布版本冻结任务契约

#### Scenario: 发布无效 Manifest 被拒绝

- **WHEN** 管理者尝试发布缺少必填约束、来源或镜像未经批准、凭据引用不可用、ref 无法解析、违反允许范围或请求不支持环境的 Manifest
- **THEN** 系统拒绝发布并返回字段级可行动原因
- **AND** 不替换当前已发布版本或留下可供 run 使用的部分快照

#### Scenario: 历史 Manifest 包含 local 发布字段

- **WHEN** 系统读取历史 Manifest 中的 local 发布字段
- **THEN** 系统仅为兼容读取而保留这些历史值
- **AND** 新建 Run 不得将其作为发布契约或权限来源
