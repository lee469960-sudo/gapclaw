## Why

CodeAgent 已能把 `secret_policy.source` / `source_unscannable` 设为 warn，但 `patch` 在政策合并层只允许 `block`，Verifier 与 Sealer 也把 patch 秘密命中一律当成 `secret_detected`。本地 `dbt_test` 改 `profiles.yml` 时，源码 warn 已放行，patch 扫描仍因 `password:` 失败，无法封存。需要显式 `patch=warn` 与 source warn 对称，且默认 block 不变。

## What Changes

- 政策合并允许 `secret_policy.patch` 为 `block` 或 `warn`；未设置时平台默认仍为 `block`。
- Verifier 与 Sealer 对 **完整** patch 扫描的秘密命中：仅当冻结政策为 `patch=warn` 时继续；截断、不支持、失败或不完整仍 fail closed（不引入 `patch_unscannable`）。
- `scan_changed_content` 在命中秘密后仍完成指纹，以便 warn 路径拿到 `content_hash`。
- 审阅 `load_verified_bundle` 与封装使用同一套冻结政策：source/patch findings 在对应 warn 下可接受；source 不完整仅当 `source_unscannable=warn` 且原因允许。
- **不做**：改平台默认、UI 文案、`patch_unscannable`、output 政策。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `code-agent-verification`: patch 阶段秘密命中受冻结 `secret_policy.patch` 约束；不完整 patch 扫描仍不得 `patch_ready`。

## Impact

- 代码：`control_plane.py` 合并允许值；`output_security.py` 完整扫描；`verifier.py` / `artifacts.py` / `review.py` 兑现 warn；`scanner.py` 增加 patch helper。
- 测试：政策层、Verifier、Sealer、审阅。
- 运行时：须发布新 Manifest 后下一 run 才带 `patch=warn`。
