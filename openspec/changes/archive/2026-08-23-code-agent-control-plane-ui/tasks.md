## 1. 控制面契约与权限基础

- [x] 1.1 定义 Code Project/Manifest 管理请求、响应和稳定错误原因，并通过 schema/API 单测验证缺失字段、非法 tier 与未授权请求不会产生部分写入
- [x] 1.2 注册 Code Project 页面路由、菜单与页面 RBAC，保持内置管理角色权限同步，并验证非授权角色无法访问页面 API 或枚举项目
- [x] 1.3 实现服务器端项目可用性 read model，区分 ready、项目停用、环境不支持、无已发布 Manifest 与 Manifest 无效，并通过逐状态单测验证结果稳定且不替代 run admission 复检

## 2. Code Project 与 Manifest 生命周期

- [x] 2.1 实现 Code Project 的可见列表、详情、创建、更新和启停 API，复用项目 ACL，并验证创建者/授权用户/无权用户的数据可见性和写权限边界
- [x] 2.2 实现 Manifest 草稿保存与字段级校验，允许不完整草稿但禁止其用于 Code run，并验证草稿保存、读取及未发布门禁
- [x] 2.3 实现 Manifest 原子发布与不可变历史版本，复用 run admission 所需的完整策略校验，并验证失败发布保留原已发布版本、已发布版本不可原地修改
- [x] 2.4 增加控制面审计与兼容性回归，验证项目/Manifest 写操作可追溯且现有 Code run 冻结契约、Standard Agent 路径不受影响

## 3. Code Project 管理界面

- [x] 3.1 新增 Code Project 管理页面、前端路由和导航入口，实现 ACL 范围内的列表、详情、创建、编辑及启停，并通过组件/API 集成测试验证状态和错误展示
- [x] 3.2 实现按仓库、路径、验证、镜像、工具和预算分组的 Manifest 草稿表单，展示字段级校验结果，并验证不完整草稿可保存但发布按钮保持不可用
- [x] 3.3 实现 Manifest 发布确认、当前版本和只读历史视图，并验证发布成功刷新项目 readiness、发布失败不覆盖当前版本

## 4. Agent 配置与运行呈现

- [x] 4.1 扩展 Agent refs/list/detail API，返回有权且已启用的 Code Project 候选、项目名称和 readiness，并验证不泄露无权项目且存量响应保持兼容
- [x] 4.2 在 Agent 创建/编辑页增加 Standard/Code Profile 与条件化项目选择，提交 `profile`/`code_project_id`，并验证现有 Agent 可切换为 Code、缺失或不可用项目阻止保存、Standard 默认行为不变
- [x] 4.3 在 Agent 卡片和编辑页展示 Profile、项目与 Manifest 状态，并验证 Code Agent 可识别、配置失效时显示可行动的项目管理入口
- [x] 4.4 完善 Agent Chat 的 Code run 终态、Verifier 证据和 sealed artifact 呈现，并验证只有 `patch_ready` 工件可进入审阅/接受，其他终态不会被标记为可采用

## 5. 集成与交付验证

- [x] 5.1 建立端到端控制面场景测试，覆盖创建项目→保存草稿→发布 Manifest→将现有 Agent 切换为 Code→提交 Code run，以及无权、停用和未发布的 fail-closed 分支
- [x] 5.2 运行完整后端测试、前端 production build、Standard/CodeAgent 兼容性测试、`git diff --check` 与 OpenSpec strict validate，并确认所有新增 Scenario 可追溯且无 Standard Agent 回归
