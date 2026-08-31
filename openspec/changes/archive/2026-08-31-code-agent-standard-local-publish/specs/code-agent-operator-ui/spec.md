## ADDED Requirements

### Requirement: Manifest 编辑界面不得显示 local 发布配置

Manifest 编辑界面 MUST NOT 显示或要求输入 local 发布命令、目标、依赖、网络目的地、发布凭据引用、验证计划或锁。界面 SHALL 告知用户标准 local 发布由平台项目档案统一管理。

#### Scenario: 编辑 Manifest

- **WHEN** 项目管理者打开或保存 Manifest 草稿
- **THEN** 页面不显示 Local 发布配置区域及相关字段错误
- **AND** 其他 Manifest 配置与普通 CodeAgent 行为保持不变
