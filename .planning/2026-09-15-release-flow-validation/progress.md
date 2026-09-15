# Progress: GitHub → Release Agent production validation

## 2026-09-15

- Started release-flow verification at `main` / `v1.0.21`.
- Confirmed latest local tag is `v1.0.21` and the release workflow uses exact tag/version matching.
- Initialized isolated Planning with Files state for the production release validation.
- No tag, commit or push has been created yet.
- Confirmed remote `v1.0.22` is unused and updated `deploy/gap.version` to `v1.0.22`.
- Release regression first pass: 115 passed; two mTLS tests were blocked before assertions by the sandbox's loopback bind restriction.
- Both relevant OpenSpec strict validations, version equality and `git diff --check` passed.
- Re-ran the two sandbox-blocked mTLS integration tests with loopback permission: 2 passed.
- Local production HTTPS baseline did not reach the application because the local TLS path failed with `SSL_ERROR_SYSCALL`; no deployment mutation occurred.
- Explicit IPv4 produced the same pre-HTTP TLS failure; local public-path probing is closed and will not be retried unchanged.
