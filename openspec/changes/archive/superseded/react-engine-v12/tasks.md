## 1. 模型下拉框包含组（agent-config）

- [ ] 1.1 `Agents.vue` 去掉 `llms.value = (...).filter((l) => l.type === 'llm')` 的类型过滤
- [ ] 1.2 新增 `singleLlms` / `groupLlms` 计算属性，按 `l.type` 拆分

## 2. 视觉区分（agent-config）

- [ ] 2.1 模型下拉框用 `el-option-group` 分「单模型」/「模型组」两组渲染

## 3. 默认模型优先单模型（agent-config）

- [ ] 3.1 `defaultLlmId()` 优先单模型（先 `MinMax`、再首个 `singleLlms` 项，空则退回）

## 4. 测试与回归

- [ ] 4.1 手动验证：新建/编辑 Agent 时模型下拉框同时出现单模型与模型组、分组可区分
- [ ] 4.2 绑定组保存成功，列表 `llm_name` 显示组名
- [ ] 4.3 默认模型仍为单模型（不默认选中组）
- [ ] 4.4 回归：既有单模型绑定行为不变
