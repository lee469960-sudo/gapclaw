## Context

三条工作线共享一个现状:`apps/api/app/services/agent_runtime/` 是单 LLM 驱动的 ReAct 运行时。`_run_modular`(runtime.py)是唯一主循环,FINAL 分支在工具执行循环之前,命中即 `return`(PASS)或 `continue`(FAIL),被跳过的工具无任何告警。`system_prompt.py::build_tools_desc` 硬编码了 ads 视图 SOP、报表 FINAL 四要素、pandas/xlsx 示例;`utils.py::_MCP_TOOL_EXAMPLES` 也含 ads 专用调用示例。这些业务口径与 `apps/api/data/skills/<id>/ads-sync-hub/references/{sync-sop,mcp-tools}.md` 高度重叠(后者已有 list→describe→query 流程与硬规则)。`docs/react-engine.md` 描述的是上一代引擎(硬门禁表、中文 FINAL 别名、已删除模块)。

## Goals / Non-Goals

**Goals:**

- 消除 FINAL+工具同轮的无声失败:不改变 FINAL 优先契约,但把"工具被跳过"显式化为可见步骤 + 教练提示。
- 让 `docs/react-engine.md` 与代码一致,成为可信的维护地图。
- 把引擎里的业务 SOP 口径收口到 Skill,引擎只保留中性、通用的引导。

**Non-Goals:**

- 不引入硬门禁/计数器来打断循环(与引擎「软提示 > 硬门禁」哲学相反)。
- 不改变 FINAL 的识别规则、不加回中文 FINAL 别名。
- 不重构 `_run_modular` 的整体结构或拆分 `runtime.py`(另行评估)。
- 不实现"FINAL 前的工具先执行"(reorder)或"工具优先、FINAL 让位"(parser 拒绝混合)——见 Decisions。

## Decisions

### D1: 防护采用「软提示 + 可见步骤」,不做 reorder,不做 parser 拒绝

在 FINAL 分支检测到 `tool_steps` 中仍有非 PLAN 工具步骤时,注入 `push_coach_hint` 与一条 `_append_step`(action=`replan`/`skipped_tools`,status 可见)。保留 FINAL 优先与「工具不执行」的既有语义。

- 备选 A(reorder,先执行工具再收尾):更符合"先做后结"直觉,但会改变 FINAL 语义、需处理"工具执行失败后 FINAL 是否还成立"的连锁,风险高。
- 备选 B(parser 拒绝混合,丢弃 FINAL 保留工具):反转现有契约,行为变化更大。
- 选 D1 是因为它最小、非破坏、且与引擎「软提示」哲学一致;唯一新增是"可见性"。

### D2: 检测点放在 FINAL 分支内,不改解析层

在 `_run_modular` 拿到 `final_step` 之后、`return`/`continue` 之前,计算 `skipped = [s for s in tool_steps if s.action != "plan" and not s.is_final]`。解析层(`tool_parser.py`)保持纯解析,不承载告警职责。

- 理由:告警是调度层语义,不是解析语义;解析层已把 step 顺序"拍平"成 action 类型序(非文档序),无法可靠重建"FINAL 前后"的意图,告警措辞只陈述事实("工具未执行"),不臆测意图。

### D3: SOP 迁出到 `ads-sync-hub` Skill,引擎保留中性引导

`build_tools_desc` 中「导出/查询策略」「报表 FINAL 四要素」「xlsx 生成示例」「依赖自装」四段 ads 相关文案迁出到 `ads-sync-hub/references/`(报表相关可新增 `references/report-sop.md` 或并入 `sync-sop.md`),与已有 `sync-sop.md` 去重合并。`utils.py::_MCP_TOOL_EXAMPLES` 中 ads 专用条目(list/describe/query_ads_view、query_ads_metric)一并迁出;`_format_mcp_tools_for_prompt` 里的 ads 硬规则分支改由 Skill 承载,引擎侧只保留「用 tools/list 返回的真实工具名 + 描述 + required」的中性目录。

- 理由:文档 §10 已声明业务口径写 Skill;引擎硬编码会使所有 agent(即使未绑定该 Skill)都背上该 SOP,且与 Skill 内容双份漂移。
- 注意:`list_notes`/`recall` 示例是否属于通用机制(而非 ads 业务)需在实现时确认——若判定为业务口径则一并迁出,否则保留。

### D4: 文档按「事实陈述」重写,不引入新约定

`docs/react-engine.md` 只描述当前行为(含 §5 由硬门禁改为软提示、FINAL 别名收窄、模块删除、新增 reflect_final/distill_final/断点续跑/native tools),不夹带新设计。文档是地图,不是规格。

## Risks / Trade-offs

- [风险] 告警文案过于频繁会稀释 coach hint 价值(模型若反复 FINAL+工具,每轮都提示)→ 缓解:告警陈述事实、不展开冗长建议;步骤告警随 `run_steps` 落盘,天然有去重/可见上限。
- [风险] SOP 迁出后,「绑定 MCP 但未绑定 ads-sync-hub Skill」的 agent 失去 ads 导出口径 → 缓解:在 `build_tools_desc` 保留一句中性提示「详细导出 SOP 见已绑定 Skill 的 references」;迁移前 grep 存量 agent 的 skill/mcp 绑定组合评估影响面。
- [风险] 迁移后 Skill references 与引擎各自演化,仍可能再次漂移 → 缓解:文档 §10 保留「业务口径只写 Skill」的维护提示,并在 tasks 里加一条"文档明确 SOP 唯一归属"。
- [风险] 文档重写易再次滞后 → 缓解:文档只陈述结构不变的部分,把易变阈值/别名标注为「以代码为准」或直接不写死数值。

## Migration Plan

1. 先合入「FINAL+工具告警」代码 + 测试(独立、无依赖)。
2. 再迁 SOP:把 ads 口径写入 Skill references → 删除引擎侧硬编码 → 跑 `test_mcp_catalog` 等断言更新(这些测试目前断言引擎目录含 `list_ads_views`,需改为断言中性目录不含 ads 硬编码)。
3. 最后重写 `docs/react-engine.md`(依赖前两步落定后的最终形态)。
4. 回滚:三线各自独立提交,可分别 revert;SOP 迁移单独一个 commit,便于在发现存量 agent 回归时单独回退。

## Open Questions

- `list_notes`/`recall`(getnote)示例是否属于业务口径、需否一并迁出——实现时按"是否 ads/getnote 专属"判定。
- 报表 SOP 是并入 `sync-sop.md` 还是新增 `report-sop.md`——取决于 `ads-sync-hub` 的 references 组织习惯,实现时确认。
