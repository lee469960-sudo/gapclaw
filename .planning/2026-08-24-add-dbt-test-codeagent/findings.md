# Findings: add dbt-test CodeAgent

## Discoveries

- Existing local development database: `apps/api/data/gap.db`.
- Existing CodeProject: `dbt_test`, id `ae834a47`.
- Existing Skill: `dbt-clickhouse-gamestat`, id `2e6b6a87`.
- Selected LLM: `M3 AND V4`, id `36bee1c3`, matching existing default agents.
- Created/updated draft CodeRepositorySource id `92d97550` for `http://g.testskydata.com:80/system/dbt-gamestat-ck.git`.
- Created/updated draft Manifest id `6c1d79cd` for project `ae834a47`.
- Created CodeAgent `dbt-test`, id `8c862281`, with `profile="code"` and skill binding `["2e6b6a87"]`.
- Project availability remains `ready=false`, `reason="manifest_missing"` because no published Manifest exists.
- Remote repository probe returned HTTP 502, so secure publish/frozen snapshot could not be completed in this turn.
- Local API `127.0.0.1:8000` was not reachable by curl during final verification, so HTTP endpoint verification was not completed.
- Root cause for save-time `repository_source_not_allowed`: `save_draft` used deployment allowlist validation, so drafts could not be saved when the current API config did not yet approve the origin.
- Fix: draft save now performs remote URL syntax normalization only; draft response can still show `validation_errors.source_locator=repository_source_not_allowed`.
- Publish remains strict because `manifest_publish` still validates source allowlist and trusted image digests.
- Compose API env now includes `CODE_REPOSITORY_ALLOWLIST` and `CODE_REPOSITORY_INTERNAL_CIDRS`, but current rendered defaults are still `[]` until set in `deploy/.env`.
- Root cause for built-in image `image_digest_not_allowed`: `CODE_TRUSTED_IMAGE_DIGESTS` was empty in both local and compose-rendered API config.
- Fix: `CODE_TRUSTED_IMAGE_DIGESTS` now includes `code-agent-runner@sha256:aed8f7617a64c887c5e7cc1ea195e9f3c7e750812dca8b3b8916eb8b052eef3f` in `apps/api/.env` and `deploy/.env`.
- Draft save now treats untrusted image digests like unapproved repository sources: save succeeds as a repairable draft, while validation reports `image_digest_not_allowed`.
- Current publish failure has advanced to `repository_unreachable`; local DNS cannot resolve `g.testskydata.com` on ports 80 or 2222.
- Added explicit trusted transport proxy support for HTTP/HTTPS repository imports: allowlist validation still applies, default DNS/IP pinning remains unchanged, and Git receives only the configured repository proxy env when enabled.
- Even with proxy mode enabled, direct `git ls-remote` to both `http://g.testskydata.com:2222/system/dbt-gamestat-ck.git` and `http://g.testskydata.com/system/dbt-gamestat-ck.git` returns HTTP 502, so the remaining blocker is clone URL/proxy/backend Git service reachability, not CodeAgent source/digest validation.
- Root cause for local `repository_network_policy_denied`: local `.env` had the HTTP source allowlist but did not enable `CODE_REPOSITORY_USE_TRANSPORT_PROXY` and did not provide repository proxy env, so HTTP source admission still used the default internal-CIDR-only network policy.
- Local API `get_settings()` is cached per process; `.env` changes require an API process restart. Uvicorn `--reload` does not watch `.env`.
- Current UI publish error `repository_unreachable` is confirmed at the Git transport layer: `info/refs?service=git-upload-pack` and `git ls-remote --heads` return HTTP 502 for both `http://g.testskydata.com:2222/system/dbt-gamestat-ck.git` and `http://g.testskydata.com/system/dbt-gamestat-ck.git` through the configured proxy.
- Direct no-proxy probing shows `g.testskydata.com` resolves to public IP `3.107.183.186`. Port `2222` is not HTTP Git; HTTP requests return `Received HTTP/0.9 when not allowed`, while SSH reaches the server and fails with `Permission denied (publickey)`.
- Direct HTTP port 80 is the correct HTTP Git endpoint shape, but it returns `401` for `info/refs?service=git-upload-pack` and `git ls-remote` cannot read a username, so a deploy credential is required.
- Added explicit `CODE_REPOSITORY_ALLOW_PUBLIC_HTTP=true` support so exact allowlisted public HTTP origins can be used without proxy; default behavior still rejects public HTTP unless this opt-in is set.
- Manifest configuration now supports creating and binding a Git HTTP deploy credential inline during draft save. The backend reuses `DeployTokenSecretStore`, stores only encrypted secret material, returns only `credential_ref`/metadata, and keeps the existing platform-admin secret assignment policy.
- The Code Projects Manifest UI now exposes credential label, Git username, and Git password/token fields. After a successful save it clears the entered secret fields, refreshes credential references, and binds the returned `credential_ref` to the draft.
- Source secret detection is now policy-controlled for published snapshots: default `policy.secret_policy.source` remains `block`, while explicit `source: "warn"` allows a complete source scan with findings to publish and records the finding count as a warning. Scan failures/incomplete scans still block, and patch/output policy remains constrained to `patch: "block"` and `output: "redact"`.
- Runtime source scan validation now reads `CodeAgentRun.effective_policy`; runs created from a Manifest with `secret_policy.source="warn"` can start from a sealed source snapshot with recorded source findings, while default/block policies still reject `secret_detected`.
- Source unscannable files are now separately policy-controlled: default `policy.secret_policy.source_unscannable` remains `block`, while explicit `source_unscannable: "warn"` allows source-only binary/unsupported files to publish as warnings. Scanner timeout/crash/input-changed/file-too-large remain blocking failures.
- Scan report `findings` can now include non-secret warning classifications (`scanner_binary_unsupported`, `scanner_unsupported_format`) so skipped paths are visible, while `findings_count` remains the count of `secret_pattern` findings only. Persisted `failure_reason` preserves the underlying scanner reason when scans are incomplete.
