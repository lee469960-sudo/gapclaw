# Design — react-engine-v14

## Context

V14 是 react-engine-v12（配置面 / 前端 `agent-config`）与 react-engine-v13（运行时面 / 后端 `agent-runtime`）的合并。两者**正交互补**——前者只动前端模型绑定 UI，后者只动后端循环收敛与视图目录，交集为空，因此 V14 是两个 capability 的组合，而非消解。

**铁律松口（用户已拍板）**：V13 R1 引入「无进展软预算」——一个静态的无进展计数器。这是对「无任何静态硬门禁」的**显式、限定范围的放宽**：仅此一个计数器，且动作是 LLM 生成的诚实总结（复用 `_distill_final`），不是粗暴截断；只在「持续无进展」时触发，不切断仍在推进的任务。V13 R2/R3 与 V12 全部不新增任何门禁。

## Goals / Non-Goals

**Goals:**
- 模型下拉框同时列出单模型与模型组，且用户能区分、能绑定组、能正确显示组名（V12）。
- 小任务不再空转到 `max_iters`：无进展时提前用诚实总结收尾（V13 R1）。
- 短任务少交一轮「精准税」：无交付物的 FINAL 跳过复核（V13 R2）。
- 具体需求能一步匹配候选视图：视图名清单 + 字段/口径映射注入 PLAN（V13 R3）。

**Non-Goals:**
- 不改后端 refs / `_validate_refs` / group failover / 环检测（V12 变更范围零后端改动；V14 整体仍改 runtime.py，来自 V13，两者不同区域）。
- 不改 MCP / Skill 内容（视图目录机制在引擎侧，靠缓存 MCP 返回）。
- 不改 group failover / 退避重试（v10/v11 已定）。
- 不加任何「仍在推进就截断」的硬停（R1 只针对无进展）。
- 不触及 Executor / Task Persistence / Checkpoint-Recovery / Retry / Concurrency / Observability / Benchmark 各维（见下）。

## Decisions

| # | 决策 | 来源 | 内容 |
|---|---|---|---|
| D1 | 能力边界 | 合并 | V14 含 `agent-config`（新）+ `agent-runtime`（改），配置面与运行时面正交 |
| D2 | 绑定组（UI） | v12 D1 | 模型下拉框去 `type==='llm'` 过滤，`el-option-group` 分「单模型/模型组」 |
| D3 | 默认优先单模型 | v12 D2 | `defaultLlmId` 先 `MinMax` 再首个单模型，不默认选中组 |
| D4 | 绑定组后端零改动（作用域限定） | v12 D3 | 不改 refs / `_validate_refs` / group failover；V14 仍改 runtime.py（来自 v13），两者不冲突 |
| D5 | 无进展软预算 | v13 D1 | `state.no_progress_streak` 达 5 且无推进 → 复用 `_distill_final` 诚实总结收尾 + `_clear_run_state` |
| D6 | 短任务跳过 `_reflect_final` | v13 D2 | `state.subtasks` 空且 `saved_paths`/`files_written` 空 → 跳过 `_reflect_final` 直接收尾 |
| D7 | 视图目录缓存+注入 | v13 D3 | 缓存 `list_ads_views` 视图名清单（跨会话 + TTL）+ 蒸馏的 `view→字段/口径` 映射，注入 task_context |
| D8 | 铁律其余不变 | v13 D4 | 除 D5 外不新增任何循环级阈值/计数器/硬停 |

## 与已应用 `agent-runtime` 规范的衔接（无冲突证据）

V13 的三条需求建立在已有基线上，逐条核对不重复、不冲突：

- **R1（无进展软预算）↔ 基线「完成度复核连续拒绝后收敛」**（`specs/agent-runtime/spec.md:368`）：两者治不同循环的收敛——基线那条治「Verifier 对 FINAL 连续 FAIL 的空转」，R1 治「主循环无任何推进（text-only 空转 / 工具全败）不到 FINAL 的空转」。互补，不重叠。
- **R2（短任务跳过复核）↔ 基线「完成度复核交付物证据」**（`:658`）：一致——有交付物才复核（并要求证据），无交付物则跳过（R2），是同一原则的正反两面。
- **R3（视图目录缓存+注入）↔ 基线「同一 MCP 工具大量调用时注入映射蒸馏软提示」（`:153`）与「绑定 MCP 时复用工具目录缓存」（`:261`）**：R3 把 `:153` 的**软性、被动**「映射蒸馏提示」升级为**结构化、主动**的视图目录注入；`list_ads_views` 的跨会话缓存**复用** `:261` 的 TTL 缓存机制（`tools/list` 缓存），不另起一套并行缓存。三者方向一致，R3 是增量。

## Risks / Trade-offs

- [风险] R1 无进展阈值 5 太小会误杀「慢但没失败」的任务。→ 缓解：推进信号宽口径（工具成功、写文件、进度、子任务推进都算推进）；阈值温和；收尾是 LLM 诚实总结、明确标注缺口，用户可续。
- [风险] R2 跳过复核可能放过短任务的错误答案。→ 缓解：短任务无交付物、答案易判，风险低；一旦有交付物仍复核（与基线「交付物证据」一致）。
- [风险] R3 视图目录缓存可能过期（schema_ads.yml 变了）。→ 缓解：缓存带 TTL / 失效策略；describe 命中候选仍会确认字段，兜底过期。
- [风险] 组与单模型同名时用户仍可能混淆（V12）。→ 缓解：`el-option-group` 分组标题已区分；名称唯一性属 LLM 管理页职责。
- [风险] 绑定组后，组自身 `max_context_tokens` 等参数不生效（取叶子成员）。→ 缓解：本变更仅放行绑定，不改变运行时取值语义；「组级参数」单列需求。

## Open Questions

- R1 无进展阈值具体取值（默认 5）与推进信号的宽口径界定。
- R3 视图目录缓存的落点（MCP 级 vs agent 级）与 TTL。
- R3 是否需要「一句话语义描述」的来源（当前只有视图名，无一句话语义；语义描述若要，需从 MCP/skill 侧补）。
- 是否需要在绑定组时给用户提示「组内降级：成员失败自动切下一个」（V12 遗留）。
- 是否需要「组级参数」覆盖（组自身 max_context_tokens / timeout 等），当前取叶子成员（V12 遗留）。
