# Design — react-engine-v5

## Context

动机见 `proposal.md`「Why」。相关现状与约束：

- **铁律**：无任何静态硬门禁——循环只由 FINAL / 用户取消 / LLM 错误 / `max_iters` 结束；所有纠正走软提示；删除冗余代码优先。V5 的唯一「计数」是完成度复核连续 FAIL 的收敛兜底（见 D4），它**不是循环门禁**，而是「避免空转到预算耗尽、丢弃正确答案」的止损——仍以软提示 / 收尾呈现，不 `break` 循环主体。
- `_save_run_state()` 目前只在 `_apply_plan()` 内、且仅当 `len(state.subtasks) >= 2` 时调用（`runtime.py:1334` 一带）。预算耗尽（`runtime.py:1093-1108`）与 LLM 连续失败（`runtime.py:844-848`）两条退出路径都不落盘，前者文案还声称「保留记录」，与实际不符。
- 原生 function-calling 路径：`push_assistant_native` 一次推入全部 `tool_calls`（`runtime.py:865`），但 `push_native_tool_result` 只对实际执行的 step 调用（`runtime.py:1073-1074`）。权限阻止（`986-998`）与 FINAL 同轮跳过（`882-896`）两条分支 `continue` / 只发提示，不补 tool 消息。
- `fit_messages_to_context` 把所有 system 层合并成一个消息再按 20000 字符保留头部（`llm_client.py:74-78`、`307-309`），而 `_run_modular` 的层序把最易变的 `progress_block → coach_hint` 放**最后**（`runtime.py:642-713`）——被裁的正是这些动态层。
- `_reflect_final` 无计数器（`runtime.py:468-470`），FAIL 后 `continue`（`921`）直接回到循环顶，跳过了 `text_only_streak` 重置与所有卡死检测；其解析出的 `fix_list`（`519-531`）在 FAIL 分支只回灌 `missing`（`920`），修复项被丢弃。

## Goals / Non-Goals

**Goals:**
- 非 FINAL 退出路径在返回前落盘最新状态，续跑真正恢复全部已落盘产物。
- 原生 `tool_calls` 在任何分支下都保持配对，消除协议 400。
- 动态软提示与进度块在裁剪后仍完整保留。
- 完成度复核拒绝循环收敛，并回灌具体修复清单。

**Non-Goals:**
- 不新增循环中断 / 强制阈值 / 计数器导致的 return/break（铁律；D4 是止损兜底，非循环门禁）。
- 不改 MCP 源端、不新增协议关键字。
- 不自动帮模型蒸馏 / 取数——软提示 only。

## Decisions

### D1: 退出路径统一落盘（Q1 根因）

在预算耗尽与 LLM 连续失败两处 `return` 之前调用 `_save_run_state(ctx, state)`（或等价持久化），且不再受「≥2 子任务」条件限制——只要有值得保留的状态（`mcp_results` / `query_cache` / `progress_lines` / `saved_paths` 任一非空）就写。
- **为何**：checkpoint 语义从「最后一次 PLAN 快照」改为「退出时最新状态」，续跑才真正可用。
- **备选**：把保存时机下沉到每次 `_materialize` 后增量写——被否，写盘频率过高，单点持久化 + 退出前 flush 更简单可靠。

### D2: 原生孤儿配对合成结果

在权限阻止与 FINAL 同轮跳过两处，若 `native and step.tool_call_id`，则调用 `cm.push_native_tool_result(step.tool_call_id, "已阻止未启用工具 …" / "FINAL 优先，未执行", clip=..)` 补一条合成结果，与真实结果同形态。
- **为何**：保证每条 assistant `tool_calls` 有配对 tool 消息；合成文案明确「未执行」原因，不影响模型判断。
- **备选**：在 `fit_messages_to_context` 里补孤儿组——被否，那是「裁剪时兜底」，无法保证裁剪前每轮都不触发 400，且裁剪只在超预算时发生。

### D3: 动态层裁剪保护

`fit_messages_to_context` 合并 system 层时，把 `coach_hint` / `progress_block` 排除在 20000 头截断之外（单独承载、或优先裁 `tools_catalog` / `skill_snapshot`）。
- **为何**：动态层是长任务最需要时（大目录 = 长任务 = 近预算）最该保留的；裁静态目录对正确性影响最小。
- **备选**：整体提高 system 上限——治标不治本，目录更大时仍会截到动态层。

### D4: 复核拒绝收敛

按 run 计数连续 `_reflect_final` FAIL；达到阈值（建议 3）后接受候选（记录并呈现被拒原因）或强制 `_distill_final` 收尾，而非继续空转。
- **为何**：过度严格的 verifier 或「实际已达标」的候选不应空转到预算耗尽再被丢弃。
- **备选**：不收敛、只靠 `max_iters` 兜底——即现状，本轮要改的就是它。

### D5: fix_list 回灌

FAIL 分支把 `fix_list`（或整个修订 PLAN）随「完成度反思」coach hint 一并注入。
- **为何**：模型需要具体缺失项才能有效重规划，笼统提示是重复循环的诱因。

### D6: `mcp_results` 有界

persist 时对 `mcp_results` 设上限（保留最近 N 条，镜像 `query_cache` 的 LRU 思路），避免多段续跑累积无界清单。
- **为何**：每条含 `args` 可能较大，无界增长拖慢续跑回填与清单回显。

### D7: 原生 role:tool 结果 in-loop 裁剪

让 `trim_tool_results` 覆盖原生 history 里的 assistant+tool 原子组（复用 `fit_messages_to_context` 已有的原子组删除逻辑），而非只裁文本 `tool_result` 层。
- **为何**：原生路径的 `cm.trim_tool_results()` 目前是 no-op，内存随轮次无界增长。

### D8: 文案与死分支清理

失败退出文案改为匹配真实持久化（落盘后说「已保留」、未落盘则说「已暂停」）；删除预算耗尽处 `if state.final:` 死分支（`_reflect_final` PASS 时已 `return`，循环耗尽时 `state.final` 恒为空）。
- **为何**：不误导用户 + 删除不可达代码（铁律）。

## Risks / Trade-offs

- [风险] D1 让 checkpoint 更频繁写入，落盘体积略增。→ 缓解：仍单点持久化，仅在退出路径多写一次；D6 已给 `mcp_results` 设上限。
- [风险] D2 合成 role:tool 结果带「未执行」文案，模型可能误以为工具真执行过。→ 缓解：文案明确「未执行 / 已阻止」，与真实结果的 `[shell]` 标题态区分。
- [风险] D3 裁静态目录可能导致模型拿不到某工具的描述。→ 缓解：目录层裁剪优先级在动态层之后，仅在极端大目录下发生，属可接受。
- [取舍] D4 引入一个计数器。→ 缓解：它是复核拒绝的止损兜底、非循环门禁，触发时仍以软提示/收尾呈现，不改变循环主体结构。

## Open Questions

- D4 收敛阈值具体值（建议 3，OpenSpec 可调）。
- D6 `mcp_results` 上限具体值（建议与 `query_cache` 的 100 对齐）。
