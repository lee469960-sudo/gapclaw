# Findings: code-agent-seal-honor-source-warn

## Pre-implementation (from grill + proposal)

- UI/run `56846267`：Verifier `passed: true`，`changed_paths: []`，`tool_calls_used: 0`；封装失败原因 `artifact_evidence_missing`；run 行 `failure_reason=infrastructure_error`。
- Source scan `b10e9848`：`incomplete`，`scanner_binary_unsupported`，`findings_count=7`（secret_pattern）；snapshot `4a73762a30f32bbd` 已 sealed。
- Manifest 已设 `secret_policy.source=warn` / `source_unscannable=warn`；publish 与 runtime 开跑已认该政策；`CodeArtifactSealer.seal()` 仍要求 `complete && findings_count==0`。
- CodeAgent 不写源 Git 仓库；UI Patch 按钮要求 `directly_adoptable`（`patch_ready` + verified + sealed）。空 diff 成功封装应为 `no_change_justified`。
- 正式范围锁在 OpenSpec change `code-agent-seal-honor-source-warn`（proposal / design / specs / tasks）。

## During execution

- `apps/api/.venv` 无 pytest；用 `.venv/bin/pip install pytest` 后 `.venv/bin/python -m pytest` 跑 sealer 测试。
- WatchFiles 只 watch `apps/api/app`，但改 `artifacts.py` 后 **未** 出现 Reloading（默认 `*.py` include 对嵌套路径可能不匹配）。3.1 必须进程重启，不能只靠 touch。

- UI facts 行把 `verified===false` 一律写成「Verifier 未通过或证据不足」，把 `sealed===false` 写成「不可用」。`no_change_justified` 成功时 API 会给 `verified=true`、`sealed=true`；若仍看到旧文案，看横幅上的 Code run id 是否还是 `56846267`。`directly_adoptable` 只认 `patch_ready`，空 diff 没有审阅按钮是设计如此（本 change 不做 UI）。

None — `design.md` Open Questions 为空；grilling 决策已写入 `task_plan.md` Decision Log 指针。
