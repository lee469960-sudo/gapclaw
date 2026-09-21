# 架构风险清单

这份清单用于新 OpenSpec change 的影响评估。历史任务不自动恢复执行；如果重新需要，必须创建新的 change 并重新验证。

| 历史变更 | 风险等级 | 主要风险 | 新 change 必须补充的验证 |
| --- | --- | --- | --- |
| `agent-runtime-resume-perf` | 高 | MCP 会话复用、失效重连、断点恢复和查询缓存可能造成状态污染、重复副作用或旧数据 | 多 MCP 分派、异常/取消关闭、旧 checkpoint 兼容、只读查询去重 |
| `react-engine` | 中高 | FINAL 行为和 ADS SOP 迁移可能改变普通对话或未绑定 Skill 的 Agent 能力 | FINAL+工具回归、普通对话、未绑定 ads Skill 的查询/导出 |
| `2026-08-21-react-engine-v14` | 中低 | 模型组 UI 可绑定，但组级参数和叶子模型运行语义可能不一致 | 单模型/模型组显示、保存、默认值、failover 和参数语义 |
| `superseded/react-engine-v12/v13` | 参考 | 已被后续设计替代，直接补做会产生重复实现 | 仅用于追溯决策，不直接复活任务 |

每个新 change 的 proposal 或 design 应说明：影响面、兼容策略、幂等边界、灰度方式、回滚方式和未绑定资源时的降级行为。
