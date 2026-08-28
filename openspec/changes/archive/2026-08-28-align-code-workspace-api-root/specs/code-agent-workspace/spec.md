## ADDED Requirements

### Requirement: Workspace 必须物化到配置的 API root

系统 SHALL 将每个 CodeAgent run 的可写 Workspace 物化到部署配置的 Workspace API root 之下，路径布局为 `<api_root>/<run_id>/workspace`。当 API root 已配置时，系统 MUST NOT 使用与该配置无关的隐式目录名物化 Workspace。当 API root 未配置时，系统 SHALL 使用 `{data_dir}/code-agent/runs` 作为唯一规范 fallback，且 MUST NOT 使用 `{data_dir}/code_agent/runs` 或其他别名。该物化根 MUST 与后续 Sandbox bind 映射所校验的 API 可见根为同一权威根。

#### Scenario: 已配置 API root 时准备 Workspace

- **WHEN** 部署已配置 Workspace API root，且 CodeAgent run 开始准备 Workspace
- **THEN** Workspace 被创建在该 API root 下的 `<run_id>/workspace`
- **AND** 系统不在其他隐式目录下创建该 run 的可写 Workspace

#### Scenario: 未配置 API root 时使用规范 fallback

- **WHEN** Workspace API root 未配置且 CodeAgent run 开始准备 Workspace
- **THEN** 系统使用 `{data_dir}/code-agent/runs/<run_id>/workspace` 作为物化路径
- **AND** 不使用 `{data_dir}/code_agent/runs` 或其他未文档化别名
