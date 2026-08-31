## Purpose

提供由平台管理、无需逐项填写 Manifest 参数的标准 local 发布流程，使 CodeAgent 能准备依赖、等待一次确认并执行可审计的发布。

## ADDED Requirements

### Requirement: 标准 local 发布档案由平台管理

系统 SHALL 为已授权 Code Project 提供一个平台管理的标准 local 发布档案。该档案 MUST 固定项目发布凭据引用、允许的发布入口、发布镜像、系统工具、网络边界和验证计划；用户与 Manifest MUST NOT 覆盖这些值。

#### Scenario: 项目使用标准发布档案

- **WHEN** 用户请求对已就绪的 Code Project 执行 local 发布
- **THEN** 系统解析该项目的标准 local 发布档案
- **AND** 不要求用户填写命令、依赖、网络、验证计划或锁

#### Scenario: 标准档案不可用

- **WHEN** 项目没有已授权的标准 local 发布档案、发布凭据或可信镜像
- **THEN** 系统在任何副作用动作前拒绝发布
- **AND** 返回平台配置不可用的稳定原因

### Requirement: 依赖准备必须受仓库声明与镜像边界约束

系统 SHALL 允许 Claude Code 扫描 Workspace 中的 CI 配置、发布脚本与锁定文件，生成依赖准备计划。系统 MUST 仅按仓库存在的锁定文件安装应用依赖，并使用固定发布镜像提供系统工具；不得基于模型猜测安装任意包或任意系统二进制。

#### Scenario: 仓库声明应用依赖

- **WHEN** 标准 local 发布的仓库含可识别的 Python 或 Node 锁定文件
- **THEN** 系统按对应锁定文件在 run-local 可写目录准备应用依赖
- **AND** 对话显示脱敏的准备结果

#### Scenario: 缺少未声明的系统工具

- **WHEN** 发布入口需要的系统工具不在固定发布镜像中
- **THEN** 系统在发布前失败并指出缺失工具
- **AND** 不允许 Claude Code 临时下载或安装该系统工具

### Requirement: 标准 local 发布只执行一次并要求确认

系统 SHALL 在执行前向用户展示发现的发布入口与依赖准备摘要，并要求一次明确确认。确认后平台 SHALL 仅执行一次已批准的 local 发布入口，采集脱敏证据并验证结果；不得自动重试副作用命令。

#### Scenario: 用户确认标准发布

- **WHEN** 发现发布入口和依赖准备均成功且用户明确确认
- **THEN** 平台执行一次 local 发布并展示退出码、发布证据、验证摘要和清理状态

#### Scenario: 发布入口不明确

- **WHEN** Claude Code 扫描到多个候选入口或无法从仓库识别安全的 local 发布入口
- **THEN** 系统不执行任何发布命令
- **AND** 要求用户在对话中选择或补充入口
