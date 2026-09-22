## MODIFIED Requirements

### Requirement: 生产 Hook 触发与标签内容隔离

系统 SHALL 使用 GitHub-hosted CI 在构建成功后向固定 `POST /internal/release-hook` 投递已验证发布清单。CI MUST 以 GitHub secret 的 HMAC-SHA256 签名覆盖规范 manifest、delivery id 与时间戳，且不得把 ACR 凭据传递给 Hook。GAP MUST 在验签、时间窗口、delivery id 去重、target、tag/version 与 digest 校验全部通过后，才通过私网 Runner deploy。生产服务器 MUST NOT 依赖 GitHub self-hosted runner、GitHub runner token 或 GitHub 出网。CI MAY retry delivery of the same signed Hook envelope for bounded transient HTTP or network failures, but MUST NOT regenerate the signed payload, signature, delivery id, or manifest between retry attempts.

#### Scenario: 构建成功后进入固定 Hook 链路

- **WHEN** 某个可部署发布清单已由构建链路产生
- **THEN** GitHub CI 仅向固定 Hook 投递签名 manifest
- **AND** GAP 仅在验签成功后调用固定 Deploy Runner

#### Scenario: Hook 重放或签名无效

- **WHEN** Hook 的签名、时间戳、delivery id 或固定 manifest 任一项无效，或 delivery id 已被接受
- **THEN** GAP 拒绝请求且不调用 Deploy Runner
- **AND** 当前运行版本保持不变

#### Scenario: 非记录镜像被请求部署

- **WHEN** 调用方请求 Deploy Runner 部署不在可部署发布清单中的镜像引用或 digest
- **THEN** Runner 拒绝该请求
- **AND** 当前运行中的 GAP 版本保持不变

#### Scenario: Hook delivery transiently fails before acceptance

- **WHEN** CI delivers a valid signed release Hook envelope and receives a transient delivery failure such as network timeout, HTTP 403, 408, 409, 425, 429, or 5xx before a successful response
- **THEN** CI retries the same Hook URL with the exact same canonical body, signature, delivery id, and manifest for a bounded number of attempts within the Hook timestamp window
- **AND** a later 2xx response completes the workflow without requiring an operator rerun
- **AND** a final non-2xx response fails the workflow with the Hook response available for diagnosis
