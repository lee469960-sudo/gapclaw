# Findings — react-engine-v1

> 执行过程中发现的问题、偏差、需决策的点记录于此。**发现一条记录一条**,不攒到收尾才补。
> 只记录「执行中新发现」,不重复 `proposal.md`/`design.md` 已记录的既定决策与风险。

## 记录格式(每条一条)

```markdown
## F<n> — <一句话标题>
- 阶段: P1–P7
- 关联任务: tasks.md <x.y>
- 严重度: blocker / major / minor / note
- 描述: <问题/偏差/需决策>
- 影响: <对 scope / 验收 / 后续任务的影响>
- 状态: open / resolved / wontfix
- 结论/处置: <若 resolved,wontfix 填原因与去向;open 留待决策>
```

---

<!-- 尚无发现。执行时按上述格式追加。 -->

## F1 — P1 任务 1.2/1.3/1.4 前向依赖 P3 的 McpSessionManager
- 阶段: P1↔P3
- 关联任务: tasks.md 1.2、1.3、1.4(引用 `McpSessionManager.close()`、会话复用/失效可观测)
- 严重度: note
- 描述: `1.2`(close 吞异常)、`1.3`(复用/失效/去重可观测)、`1.4` 后半(close 不残留子进程)都引用了 `McpSessionManager`,而该类在 `3.1` 才创建。tasks.md 把可观测基座放 P1、把类实现放 P3,存在前向引用。
- 影响: 执行顺序上,1.1 可独立完成;1.2/1.3/1.4 需待 3.1 落定 `McpSessionManager` 后才能收口。不改变 scope,仅影响顺序。
- 状态: resolved
- 结论/处置: 先完成 1.1 + 聚合测试;先做 P3(3.1–3.6)建 `McpSessionManager`,再回来收口 1.2/1.3/1.4,最后 P2、P4、P5、P6、P7。

## F2 — 查询去重语义落点在 McpSessionManager(与 design D7 原文字面不符)+ LRU 常数需调优
- 阶段: P4
- 关联任务: tasks.md 4.5
- 严重度: minor
- 描述: `design.md` D7 原写「query_cache 与 mcp_result_seq 记账全在 `_run_modular`」。实现时发现 `McpSessionManager` 天然持有 per-run 生命周期、`mcp.id` 与 `async with` 关闭路径,把 dedup 判定 + 落盘记账 + `mcp_result_seq`(=`len(mcp_results)`)放进去可避免 `_run_modular` 再膨胀,且纯 transport 函数 `call_mcp_tool` 保持「一次调用 = 一次连接」不变(MCP 测试端点零影响)——D7 的核心原则(纯 transport 不被污染)得以保留,仅「落点文件」从 runtime.py 改为 mcp_client.py 内的 per-run 编排类。
- 影响: 不改变验收行为(去重、两层生命周期、LRU+大小上限均已实现);仅调整 design.md D7 措辞以反映事实落点。LRU 条数(`max_cache_entries=100`)与单条 size 上限(`max_cached_result_chars=2_000_000`)为 Open Questions 所列「依赖实测 blob 增长量」的初值,后续可调。
- 状态: resolved
- 结论/处置: 更新 design.md D7 末段为「per-run 编排层」表述;`McpSessionManager.__init__` 暴露 `max_cache_entries`/`max_cached_result_chars` 两参数,`_trim_query_cache` 做 FIFO(丢最旧)驱逐;新增 `test_query_cache_lru_evicts_oldest` 覆盖。

## F3 — ads-sync-hub Skill 无本仓库内版本化 seed 源(report-sop.md 落在 gitignored data/)
- 阶段: P5
- 关联任务: tasks.md 5.1、5.2、5.4
- 严重度: minor
- 描述: `ads-sync-hub` Skill 不在 `seed_assets/`(seed.py 只 seed `system-log-analyst`/`tushare-data`),其运行副本在 `.gitignore` 的 `apps/api/data/skills/e05b2cf5/ads-sync-hub/`。故新建的 `references/report-sop.md` 写入运行时 data 目录,功能生效但不被本仓库 git 跟踪/版本化。
- 影响: 引擎侧口径迁出(代码)已版本化;Skill 侧 `report-sop.md` 内容仅在运行时 data 下。若 `ads-sync-hub` 由外部源(如独立 skill 仓库/上传)重新 seed,`report-sop.md` 需一并补进该外部源,否则新 seed 会丢失报表口径。
- 状态: open
- 结论/处置: 已把迁出的报表 SOP 写入 `apps/api/data/skills/e05b2cf5/ads-sync-hub/references/report-sop.md` 并更新其 `SKILL.md` 指引;遗留「外部 seed 源同步 report-sop.md」待用户确认该 Skill 的权威管理位置。
