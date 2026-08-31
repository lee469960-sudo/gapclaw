## ADDED Requirements

### Requirement: 标准发布 runner 必须固定系统工具并受控准备应用依赖

标准 local 发布使用的可信 runner 镜像 SHALL 在构建时包含平台批准的系统工具版本。运行时 SHALL 只根据 Workspace 内可识别的锁定文件在 run-local 可写目录准备应用依赖；不得以 root 权限安装系统包、不得修改基础镜像，也不得将依赖写入业务提交或 sealed patch。

#### Scenario: 镜像提供系统工具

- **WHEN** 标准 local 发布 runner 启动
- **THEN** 预检记录固定系统工具的版本或缺失事实
- **AND** 不需要用户在 Manifest 中列出这些工具

#### Scenario: 锁定文件驱动应用依赖准备

- **WHEN** Workspace 含受支持的应用依赖锁定文件
- **THEN** 系统将依赖安装到 run-local 可写目录并记录脱敏结果
- **AND** 业务 Workspace 文件和 Git 状态不因准备操作改变
