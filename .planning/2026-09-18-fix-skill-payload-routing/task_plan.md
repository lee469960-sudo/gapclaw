# Plan: fix-skill-payload-routing

## Formal Source

`openspec/changes/fix-skill-payload-routing/tasks.md` 是唯一正式任务来源。

## Phases

1. Reproduce — task 1.1: 完整请求与入口回归测试。
2. Fix — task 2.1: 共用风险判断、正文边界与绑定校验。
3. Verify — task 3.1: modular Skill 读取写入、风险确认、受影响回归；task 3.2/3.3: 能力语义路由；task 3.4: 首轮 tool-free FINAL 纠偏；task 3.5: 稀疏元数据结构化任务入口；task 3.6: WebSocket 丢失 done 的前端状态恢复。

## Current Phase

Phase 3 — complete. Tasks 1.1, 2.1, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6 have corresponding code/test evidence in progress.md.
