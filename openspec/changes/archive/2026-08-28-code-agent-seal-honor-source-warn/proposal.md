## Why

CodeAgent 已能按 Manifest `secret_policy.source=warn` / `source_unscannable=warn` **发布快照并启动 run**，但封装 sealer 仍要求 source scan `complete && findings_count==0`。Verifier 通过后封装以 `artifact_evidence_missing` 失败，用户看不到终态成果（例如空 diff 的 `no_change_justified`）。现在要走通第一枪：在不放宽 **patch block** 的前提下，让封装与已冻结的 warn 政策一致。

## What Changes

- 封装阶段对 **source scan / snapshot** 的证据门槛与 publish/runtime 使用同一套 `secret_policy` helper：`source=warn` 允许 source finding；`source_unscannable=warn` 允许 `incomplete` + `scanner_binary_unsupported` 类警告。
- 默认 `block` 行为不变：source 密钥或不可扫描文件在未 warn 时仍不得封装成功。
- **patch 扫描仍 block**：canonical diff / 待封存新增内容命中秘密或 patch 扫描不完整，仍不得 `patch_ready`。
- 封装时 **不**对已 sealed snapshot 做全量重扫。
- 空 canonical diff 且证据齐全时，封装成功终态为 `no_change_justified`（现有语义，须在 warn 路径下也能到达）。
- **不做**：UI 文案、`failure_reason` 落库、清理源仓库、Agent 工具调用/第二枪改文件。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `code-agent-verification`: 封存门槛区分 **source 阶段**（受冻结 `secret_policy.source` / `source_unscannable` 约束）与 **patch 阶段**（始终 fail-closed）。不得再把已 warn 放行的 source 扫描结果当成封装缺失证据。

## Impact

- 代码：`apps/api/app/services/code_agent/artifacts.py`（sealer source 证据检查）；复用 `scanner.py` 已有 helper，避免第三套政策解释。
- 测试：`apps/api/tests/test_code_agent_artifacts.py`（warn 可封、默认 block 仍拒、patch secret 仍拒）。
- 运行时：已发布且 `source`/`source_unscannable` 为 warn 的项目（如 `dbt_test`）在 Verifier 通过后应能封装；空 diff → `no_change_justified`。
- 范围外：Operator UI、Manifest 发布、runtime 开跑校验、Git 写回。
