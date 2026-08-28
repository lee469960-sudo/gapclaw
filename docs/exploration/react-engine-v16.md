# react-engine-v16 需求文档

> 本轮目标：**用最少的轮次精准完成任务**。基于 `[dba]` Agent 三次真实运行的逐轮复盘，定位并修正「空转」「瑕疵漏检」「重复」三类轮次浪费。

## 1. 背景与根因（session 89f73ff2 实测）

`dba` agent（`max_iterations=200`）导出 16 列充值用户数据，三次运行现象与根因：

| # | 现象 | 证据 | 根因 |
|---|---|---|---|
| 1 | 第一次 200 轮未完成，107→200 **空转 94 轮** | 107–200 每轮 0 工具，preview 全是「任务已完结/最终交付 xlsx」复读；全程 `FINAL-related steps = 0` | 模型在 103 轮输出 FINAL 被 Verifier 拒绝（replan）后，**再也不输出 `FINAL:`/`done`，只写「任务完成」自然语言**。引擎只认协议行，没有「完成信号→结束」识别 |
| 2 | 第二次 24 轮完成但**小瑕疵** | 「渠道名称没有映射」在两轮 Verifier 拒绝（it=21/22）后仍漏过 | `_reflect_final` 把 goal 截到 1500 字、明确写「小瑕疵一律 PASS」，不逐条核对 16 列口径 |
| 3 | 第三次准确描述瑕疵后**大量重复**，72 轮仍可能耗尽 | RUN2 前 13 轮全在 `awk/cat` 反复读 `build_report.py`、`ls mcp_result_*.json` 重新定位 | resume 无「已完成清单」注入，模型只能重读历史 |
| 4 | SQL 层 31% 精确重复 | `execute_ads_sql` 86 次 → 归一化后 59 唯一、**27 精确重复**，主查询同一 SQL 跑 16 次、另一条 11 次 | 去重只覆盖 READ/SHELL/SEARCH 与 MCP query/describe，**未覆盖 `execute_ads_sql`**；大结果截断/落盘后无「已落盘 path，请 READ 取回」指针 |

> 附带确认：`describe_ads_view` 仅 12 次、`list_ads_views` 仅 1 次 —— 视图目录（v15 R3）已生效，**不是** describe 刷屏问题；真正的重复在 SQL 执行层。

## 2. 决策汇总（grill 结果）

| # | 维度 | 决策 |
|---|---|---|
| Q1 | 完成信号处理 | A：完成信号软转换（完成信号文本 → FINAL 候选 → Verifier 把关） |
| Q2 | Verifier 核对方式 | A：需求拆 checklist，逐条核对，去「小瑕疵 PASS」豁免 |
| Q3 | 重复治理 | C：已完成清单注入 + 语义去重 + trim 策略 |
| Q4 | 完成信号检测机制 | C：正则粗筛 + 连续 2 轮疑似才 LLM 确认 |
| Q5 | Verifier 拒绝后衔接 | A：定向补（只补失败项，保留完成态，不推翻） |
| Q6 | 已完成清单来源 | A：引擎零 LLM 拼（子任务 + saved_paths + progress + 已尝试摘要） |
| Q7 | 语义去重判据 | A：规范化参数去重（视图名/SQL 归一化），不做 LLM 判等 |
| Q8 | SQL 重复治理 | A：`execute_ads_sql` 归一化去重 + 大结果取回指引 |
| Q9 | checklist 来源 | A：Verifier 现场按 goal 拆（不预拆缓存） |
| Q10 | 完成信号范围 | A：只认正向完成，不认负向「无法完成」 |

## 3. 需求

### 需求 1：完成信号软转换（治空转）

当模型输出正向完成声明（含「已完成/任务完成/最终交付/已交付/无需再调用工具」等 + 本轮无工具调用 + 非疑问句）时，引擎 MUST 连续 2 轮疑似后调用一次 LLM 确认；确认后将文本当作 `FINAL` 候选送入 `_reflect_final`。该转换 MUST 只认正向完成、MUST NOT 认负向「无法完成/无法继续」；MUST NOT 新增确定性停止门（仍由 Verifier / `FINAL` / 取消 / LLM 错误 / `max_iters` 决定结束）。

- **WHEN** 连续 2 轮输出正向完成声明文本且无工具调用
- **THEN** 引擎用一次 LLM 判断「是否为完成声明」
- **AND** 确认为完成声明后，把文本作为 FINAL 候选送 `_reflect_final`
- **AND** 若检测到「无法完成/不能/吗/？」等否定或疑问，MUST NOT 触发转换

### 需求 2：需求感知 Verifier（治瑕疵漏检 + 过度拒绝）

`_reflect_final` MUST 现场把 `goal` 逐条拆成核对项（列/字段/口径/补充说明），逐项判 PASS/FAIL；对数据准确性任务，字段口径/数字/映射关系 MUST 精确，仅排版措辞豁免（不再「小瑕疵一律 PASS」）。FAIL 时 MUST 只定向补缺失项、保留已完成子任务 `[x]` 与已写文件，不推翻完成态。

- **WHEN** Verifier 收到 FINAL 候选
- **THEN** 逐条列出核对项并判 PASS/FAIL，FAIL 项进修复清单
- **AND** 修复清单只针对失败项，已完成子任务与文件保留
- **AND** 「渠道名称未映射」这类字段口径缺失 MUST 判 FAIL

### 需求 3：已完成清单注入（治重新定位）

引擎 MUST 每轮在 task_context 稳定层回显「已完成子任务（勾选）+ 已写文件路径 + 进度后 N 条 + 已尝试工具摘要」；resume 时额外注入「上次执行到此、还差 X」。该清单 MUST 由引擎零 LLM 拼（复用 progress_lines / saved_paths / subtasks / tool_call_tally / query_cache），MUST NOT 被 `trim_tool_results` 裁剪。

- **WHEN** 任意轮次（含 resume 后首轮）构建任务上下文
- **THEN** 注入已完成清单（子任务 + 文件 + 进度 + 已尝试工具）
- **AND** 清单不因上下文裁剪而丢失

### 需求 4：SQL 执行去重 + 大结果取回指引（治 SQL 重复）

`execute_ads_sql` MUST 按 SQL 归一化 hash 作为 key 进入 `query_cache`；命中时 MUST 回显「该 SQL 结果已缓存/已落盘 `path`，请 READ 取回，勿重跑」；结果过大落盘后 MUST 明确回显取回路径，而非只回显截断片段。

- **WHEN** 模型再次调用与已执行 SQL 归一化后相同的 `execute_ads_sql`
- **THEN** 命中缓存，回显「已缓存/已落盘 path，请 READ 取回」
- **AND** 大结果落盘后回显完整取回路径

## 4. 铁律与边界

- **铁律不变**：无任何静态硬门禁；循环仍只由 `FINAL`（经 Verifier）/ 用户取消 / LLM 错误 / `max_iters` 结束。需求 1 的「完成信号软转换」因 Q4=C（LLM 确认）+ Verifier 兜底而保持软性，不新增「放弃/截断」类确定性停。
- **负向信号**：「无法完成/无法继续」不纳入软转换，仍走现有 `FINAL:` 路径。
- **SQL 归一化**：去空白/大小写/尾分号；`LIMIT/OFFSET` 不同视为不同查询（合法分页不去重）。
- **不触碰**：group failover / 环检测 / 退避重试 / MCP 连接熔断 / Executor（v10/v11 已定）。

## 5. 相对 v15 的关系

v16 在已 apply 的 v15 之上**新增** 4 条需求，与 v15 互补、无冲突：
- 需求 1 补 v15 R1′（破局提示）覆盖不到的「声明完成但无 `FINAL`」空转。
- 需求 2 强化 v15 的 `_reflect_final`（从「模糊实质完整」→「逐条口径核对」）。
- 需求 3/4 是新增（v15 无清单注入、无 SQL 执行去重）。

## 6. 下一步

- 落点：新建 `openspec/changes/react-engine-v16/`（自包含，build 于已 apply 基线）。
- 待 `openspec validate react-engine-v16 --strict`。
