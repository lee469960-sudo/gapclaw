## 1. Release Hook delivery retry

- [x] 1.1 Update the CI Hook delivery step to retry the same signed envelope on bounded transient HTTP/network failures, including the observed HTTP 403, and verify workflow contract tests assert same body/signature/delivery id reuse.
- [x] 1.2 Preserve fail-closed behavior for invalid Hook inputs and duplicate delivery ids; verify existing release Hook API/service tests still pass.

## 2. Verification

- [x] 2.1 Run focused workflow/release Hook tests, strict OpenSpec validation, and diff checks; verify no Agent, MCP, channel, or Runner authority boundary changes are introduced.
