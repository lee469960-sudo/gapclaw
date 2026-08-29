## ADDED Requirements

### Requirement: Claude Code 目标定位失败必须以 target_not_found 退出

当 `claude_code` run 的任务要求替换或修改明确目标值，且 runtime 在允许范围内无法找到该目标值时，系统 SHALL 以公开非成功终态 `target_not_found` 结束该 run。该终态 MUST 不被映射为基础设施失败、模型失败、Verifier 失败或 `no_change_justified`。`target_not_found` MUST NOT 生成 patch 或 sealed artifact，并 SHALL 向用户展示被搜索的目标、搜索范围和不含秘密的定位摘要。

#### Scenario: 旧 IP 未找到

- **WHEN** 用户要求将 `192.168.10.36:5432` 替换为 `192.168.0.105:5432`
- **AND** Claude Code 在冻结允许路径内未找到旧目标值
- **THEN** run 以 `target_not_found` 结束
- **AND** 结果说明未找到 `192.168.10.36:5432`
- **AND** 不生成业务文件 patch

### Requirement: Claude Code 多候选或范围不清必须以 needs_user_decision 退出

当 `claude_code` run 发现多个可能目标，且自动选择会改变环境、profile、dev/local/prod 边界或其他高风险配置语义时，系统 SHALL 以公开非成功终态 `needs_user_decision` 结束该 run。系统 SHALL 列出候选文件、命中摘要和建议问题，并保留不可执行 Workspace 到正常保留期。系统 MUST NOT 自动选择候选、批量修改所有候选或保持 runner 长时间等待用户输入。

#### Scenario: 多个 local/profile/dev 候选

- **WHEN** 任务定位到多个可能的 local/profile/dev 配置文件或命中项
- **AND** 无法从任务契约确定唯一目标
- **THEN** run 以 `needs_user_decision` 结束
- **AND** 结果列出候选文件与不含秘密的命中摘要
- **AND** runner 被停止，Workspace 按保留策略进入不可执行状态

### Requirement: Claude Code 任务结果必须区分平台验收和业务目标结果

系统 SHALL 区分平台链路是否可用与具体业务目标是否完成。若 Claude Code 成功启动、加载授权 Skill 并进入 coding loop，但业务目标值不存在，平台验收 MAY 视为链路正确，业务 run 仍 SHALL 以 `target_not_found` 结束。

#### Scenario: 平台链路正常但业务目标不存在

- **WHEN** Claude Code preflight 通过并进入 coding loop
- **AND** 任务目标值不存在于允许范围
- **THEN** 平台验收可记录 runtime 链路已正常工作
- **AND** 该业务 run 的用户可见终态仍为 `target_not_found`
