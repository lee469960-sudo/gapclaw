## 1. Profile、项目策略与持久化基础

- [x] 1.1 在 Agent 模型、schema、迁移和 Agent API 中增加可选 `profile`（默认 `standard`），并验证未携带该字段的存量创建、读取和运行请求保持原有语义
- [x] 1.2 定义并持久化版本化 Code Project Manifest、有效策略和冻结任务契约（仓库、base commit、路径、验证与预算），并验证缺失/失效 Manifest 不会创建可写 Code run
- [x] 1.3 实现平台→组织→项目→Profile→任务的单向收紧策略合并与稳定拒绝原因，并验证下层配置无法扩大路径、网络、工具或预算权限
- [x] 1.4 在 Agent 配置与任务入口增加 Code Profile 的显式选择和项目授权校验，并验证未授权主体及无 Manifest 的修改任务被拒绝、Standard 配置不受影响

## 2. 统一 Runtime 的 Profile 编译与兼容性

- [x] 2.1 在 `run_agent`/`AgentContext` 构建处实现 Profile resolver 与不可变 CodeExecutionContext，并验证 Code 上下文只在显式 `code` Profile 中出现
- [x] 2.2 将 Code 准备、验证、封存和终结阶段作为统一事件 envelope 的版本化 Profile payload 接入，并验证旧事件消费者可忽略未知 payload
- [x] 2.3 将 Code run 的取消、超时、异常与资源回收接入既有 Runtime 终结路径，并验证任一终止路径阻止后续工具动作且报告清理失败
- [x] 2.4 增加 Standard Profile 兼容性回归，验证既有工具路由、终态、事件通用字段和无 Profile 的默认执行均与变更前一致

## 3. 受管 Workspace 与隔离 Runner

- [x] 3.1 实现 Workspace manager：从受管仓库固定 commit 创建每 run 独立工作区、记录源码事实并禁用 checkout 期间的仓库代码执行；验证并发 run 不共享可写文件
- [x] 3.2 实现 V1 受限容器 runner 的资源、挂载、默认断网和镜像事实报告，并验证其拒绝宿主敏感挂载、特权/嵌套容器与未批准网络
- [x] 3.3 在写入与封存前实现 Workspace 完整性校验，并验证基线漂移或不可解释外部写入产生 `workspace_integrity_error` 且不交付 patch
- [x] 3.4 实现正常结束、取消、超时和失败后的 Workspace 清理与短期留存策略，并验证未封存临时内容不可下载为交付物

## 4. Code Tools 与策略执行

- [x] 4.1 实现类型化的 read、search、edit/patch 和 test 工具适配器，验证每个调用均受 Workspace、允许路径、资源预算和审计记录约束
- [x] 4.2 实现受限 shell 与只读 Git 查询能力，验证 V1 拒绝提交、推送、PR、任意命令逃逸、动态工具加载和未授权外部访问
- [x] 4.3 实现工具执行前的确定性策略预检和安全的模型反馈，验证拒绝结果不泄露受保护路径或内容且仓库文本不能提升权限
- [x] 4.4 为工具输出增加敏感信息分类/脱敏与项目 ACL 工件控制，并验证秘密样式内容不会出现在普通用户事件、日志或模型反馈中

## 5. 强制验证、封存与结果模型

- [x] 5.1 实现独立 Verifier 阶段，执行冻结的测试计划、受保护路径、秘密检测和测试完整性检查；验证模型 FINAL 或测试声明不能绕过该阶段
- [x] 5.2 实现验证基线记录与失败分类（新增失败、既有失败、环境不可用），并验证未完成/不确定验证永不标记为 `patch_ready`
- [x] 5.3 从冻结 Workspace 生成 canonical diff、base commit/diff hash/策略/镜像/Verifier 报告工件集合，并验证工件缺失或封存失败返回 `infrastructure_error`
- [x] 5.4 实现 CodeAgent 稳定终态与 UI/API 展示，包括 `patch_ready`、`stale`、`no_change_justified`、`verification_inconclusive`、`budget_exhausted`、`no_progress`、策略及基础设施失败，并验证未验证 patch 显著标记为不可直接采用
- [x] 5.5 实现人工审阅的 patch、验证证据和工件哈希视图/下载接口，并验证接受动作仅引用封存工件且不产生 Git 写操作

## 6. 安全运营、灰度与回归验证

- [x] 6.1 实现全局及项目、仓库、工具、镜像、模型粒度的 Code Profile kill switch，并验证停止新 Code run 不影响 Standard Agent
- [x] 6.2 实现 Code run 的独立队列、项目并发/资源配额及可观察预算消耗，并验证超限进入 `budget_exhausted` 而非静默降低安全或验证级别
- [x] 6.3 建立 CodeAgent 安全负向测试，覆盖提示注入、越权路径、秘密回显、网络/命令逃逸、受保护测试修改、取消清理与 Workspace 污染
- [x] 6.4 建立 CodeAgent evaluation/试点观测工件，验证三个 allowlist 内部项目的十个低风险任务能记录验证可复现性、人工接受率、成本、耗时与清理结果
- [x] 6.5 运行完整后端、前端、Runtime 兼容性和 OpenSpec 严格校验，验证所有 CodeAgent specs 场景与 Standard Agent 零回归要求均可追溯

## 7. Verify 缺口的 fail-closed 修复

- [x] 7.1 在 Code run admission 强制执行 `internal_non_production` 项目环境门禁，并验证 production 或其他未支持 tier 即使具有 published Manifest 也不会创建 run 或 Workspace
- [x] 7.2 为 Code Tool、验证基线与最终 Verifier 的真实容器命令实现冻结 deadline 和强制终止，并验证超时阻止后续动作、产生显式超时终态且进入统一清理
- [x] 7.3 将 Workspace 准备和 runner 启动纳入从首项资源分配开始的补偿清理与终态持久化，并验证启动失败不会遗留可写 Workspace、可恢复 `pending` run 或静默清理失败
- [x] 7.4 对所有可封存文本、大文件与二进制变更执行完整且有界的秘密扫描，并验证扫描截断、格式不支持或失败均 fail closed 且永不产生 `patch_ready`
- [x] 7.5 建立上述四项的安全负向回归，运行完整后端、前端、Runtime 兼容性及 OpenSpec strict validate，并确认所有新增场景可追溯后重新执行 change verification
