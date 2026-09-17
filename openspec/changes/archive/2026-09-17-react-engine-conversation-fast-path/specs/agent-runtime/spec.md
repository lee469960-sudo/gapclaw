## MODIFIED Requirements

### Requirement: 最终回复复核依据子任务完成度

对候选 FINAL 做完成度复核时，复核输入 MUST 包含渲染后的子任务清单与最近进度，使复核能按子任务完成度判定，而非仅依据目标与已保存文件。对于 `chat` 模式的有效普通文本，运行时 MUST 跳过该复核；`task` 和 `tool` 模式仍按子任务完成度复核。

#### Scenario: 任务模式复核输入含子任务清单与进度

- **WHEN** 引擎在 `task` 或 `tool` 模式对候选 FINAL 执行完成度复核
- **THEN** 复核提示包含渲染后的子任务清单与最近进度

#### Scenario: 复核输入含子任务清单与进度

- **WHEN** 引擎对候选 FINAL 执行完成度复核
- **THEN** 复核提示包含渲染后的子任务清单与最近进度

#### Scenario: 普通对话跳过完成度复核

- **WHEN** 引擎在 `chat` 模式收到有效普通文本
- **THEN** 引擎直接收尾
- **AND** 不创建子任务复核轮次

## ADDED Requirements

### Requirement: ReAct 循环必须受模式预算与无进展保护

运行时 MUST 在每个请求开始时确定 `chat`、`task`、`tool` 或 `human_wait` 模式，并按模式应用轮次预算。任一自动循环连续两轮无工具、文件、计划、外部状态或用户信息进展时 MUST 终止，不得仅依赖模型自行输出 FINAL。

#### Scenario: 任务循环无进展停止

- **WHEN** `task` 或 `tool` 模式连续两轮没有可验证状态变化
- **THEN** 运行时停止继续推理
- **AND** 返回可解释的停止状态或人工闭环状态
