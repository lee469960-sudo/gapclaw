# Task Plan: add dbt-test CodeAgent

## Goal

Create one usable CodeAgent named `dbt-test`, bound to the existing `dbt-clickhouse-gamestat` skill and the configured dbt gamestat repository.

## Assumptions

- This is an operational data/configuration task, not a new OpenSpec implementation change.
- Prefer existing API/control-plane/seed patterns over schema or runtime code changes.
- Do not modify unrelated dirty worktree files.

## Phases

### Phase 1: Discover Existing State

- Locate database/service configuration.
- Identify existing skill id for `dbt-clickhouse-gamestat`.
- Identify or create a CodeProject/Manifest for the dbt repository.
- Identify suitable LLM/default policy values from existing agents.

Status: complete

### Phase 2: Create Or Update Agent

- Create or update `dbt-test` with `profile="code"`.
- Bind the dbt CodeProject.
- Bind `dbt-clickhouse-gamestat` skill.
- Keep operation idempotent.

Status: complete

### Phase 3: Verify

- Confirm the Agent exists and serializes through the existing API/data model.
- Confirm project availability/readiness state.
- Record any blockers if runtime security readiness prevents usable CodeAgent execution.

Status: partial - Agent exists; draft save bug fixed; CodeProject is not runnable until Manifest is published.

## Errors

| Time | Error | Resolution |
| --- | --- | --- |
| 2026-08-24 | `git ls-remote` for `http://g.testskydata.com/system/dbt-gamestat-ck.git` returned HTTP 502. | Created draft source/Manifest and Agent binding; publish remains blocked until repository access succeeds. |
| 2026-08-24 | Manifest draft save failed with `repository_source_not_allowed` before publish. | Relaxed draft save to syntax-normalize remote URLs without deployment allowlist; publish/admission still enforce source policy. |
| 2026-08-24 | Built-in runner image failed with `image_digest_not_allowed`. | Added the built-in runner digest to local/compose env and made draft save treat untrusted image digest as a repairable validation error instead of a hard save failure. |
| 2026-08-24 | Publish failed with `repository_unreachable` because local DNS cannot resolve the Git host and direct Git access through proxy returns HTTP 502. | Added explicit trusted transport proxy mode for HTTP/HTTPS repository imports; remaining blocker is verifying a Git clone URL/proxy/backend that returns non-502 to `git ls-remote`. |
| 2026-08-25 | Direct HTTP Git reached the repository but returned `401` because no deploy credential was bound. | Added Manifest UI/backend flow to create a Git HTTP deploy credential from username/password during draft save and bind the resulting `credential_ref`. |
