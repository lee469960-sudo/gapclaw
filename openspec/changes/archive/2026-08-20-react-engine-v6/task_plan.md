# Task Plan — react-engine-v6

> 执行映射。**不重新定义需求**——需求的唯一正式来源是 `tasks.md`（其又源自 `specs/agent-runtime/spec.md`、`specs/channels/spec.md` 与 `design.md`）。本文件只把 `tasks.md` 的 5 组任务映射为可执行阶段，并标注依赖顺序与验收口径。

## 执行阶段映射

| 阶段 | 对应 tasks.md 组 | 主题 | 依赖 |
|---|---|---|---|
| A | 1.x | TG 入站 document 下载并交给 Agent（新能力 `channels`） | 无 |
| B | 2.x | LLM 传输重试加强（5 次 + 指数退避 + 抖动） | 无 |
| C | 3.x | 传输 vs 400 区分计数 + 报错带端点 | B（复用 2.x 的异常类型标记） |
| D | 4.x | SQL 降轮次提示词强化（软引导，无新机制） | 无 |
| E | 5.x | 测试与回归 | A/B/C/D 全部实现之后 |

## 执行顺序与理由

1. **先 A**：TG document 下载是独立新能力（`channels`），不触碰 agent-runtime 循环，改动面集中在 `telegram.py`，先做降低后续回归噪声。
2. **再 B**：传输重试加强是 `llm_client.py` 内聚改动，独立可测；其产出的「传输错误异常类型标记」是 C 的前置。
3. **C 在 B 之后**：`runtime.py` 的传输/400 区分计数依赖 2.x 抛出的可区分异常（`design.md` D5），待 B 落地后再接。
4. **D 随时可做**：纯 `system_prompt.py` 措辞强化，零代码快赢，可与 A/B 并行。
5. **E 收尾**：覆盖 1–4 各组的单测，最后跑全量回归并确认无新增循环门禁/静态阈值。

## 验收口径（对齐 7 条铁律）

- 每个任务勾选前，必须同时满足：`tasks.md` 对应行的 acceptance 已实现 + 有测试覆盖（E 组单测） + 全量回归绿。
- 不新增任何静态硬门禁 / 循环级计数器 / 阈值（`design.md` 铁律）：C 阶段的「传输连续 3 次 / 400 连续 2 次终止」是既有 `llm_failures` 机制的精化，非新增循环门禁。
- 实现中发现的问题一律记 `findings.md`，完成事实记 `progress.md`，两者都不动 `tasks.md` 的勾选逻辑。
