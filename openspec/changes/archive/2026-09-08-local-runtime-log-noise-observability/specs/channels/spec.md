## ADDED Requirements

### Requirement: Telegram poll failures are structured and rate-aware
Telegram polling MUST log failures with structured non-secret context and observable retry or backoff state so operators can distinguish current instability from historical failures.

#### Scenario: Telegram poll failure includes safe context
- **WHEN** Telegram polling fails for a channel
- **THEN** the log event includes the channel identifier, failure class, exception type, and retry or backoff state without logging bot tokens

#### Scenario: Repeated Telegram poll failures are not unbounded noise
- **WHEN** the same Telegram channel continues failing with the same failure class
- **THEN** diagnostics expose the repeated failure count or interval without requiring one full traceback per polling attempt

#### Scenario: Successful poll after failures is observable
- **WHEN** Telegram polling succeeds after one or more failures
- **THEN** diagnostics expose that the failure streak recovered so recent health is distinguishable from all-time failure totals
