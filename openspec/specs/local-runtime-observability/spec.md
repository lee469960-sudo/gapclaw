# local-runtime-observability Specification

## Purpose
Defines the local runtime observability contract for GAP development deployments, including health signals, structured log categories, noise control, and compatibility with existing local log sources.

## Requirements

### Requirement: Local health signals distinguish component state from public reachability
The system SHALL expose local health evidence that separates API/Web listener health, cloudflared local process health, public tunnel reachability, proxy influence, and IM event log freshness.

#### Scenario: API and Web local health are checked independently
- **WHEN** a local health check runs
- **THEN** the result identifies whether the API and Web components are listening locally and whether their local HTTP probes return a successful or redirect response

#### Scenario: Public tunnel probe reports proxy influence
- **WHEN** a public tunnel probe runs while proxy environment variables are configured
- **THEN** the result reports that proxy variables are present and the public probe result is distinguishable from local proxy availability

#### Scenario: IM event log source uses the real table
- **WHEN** the IM event log source is checked
- **THEN** the check reads the `im_event_logs` table while preserving `im_events` as the external source name

### Requirement: Runtime log analysis distinguishes recent and cumulative failure frequency
The system SHALL provide log analysis output that distinguishes a recent observation window from all-time log totals for known high-signal failure classes.

#### Scenario: Recent and cumulative counts are both available
- **WHEN** log analysis reports Telegram poll failures, LLM throttling, connection failures, polling noise, or no-progress hints
- **THEN** the output includes both a recent-window count and an all-time count for each reported class

#### Scenario: Recent errors include top exception categories
- **WHEN** log analysis reports recent runtime errors
- **THEN** it includes the most frequent exception/error tokens in the recent window without requiring a full log scan by the operator

### Requirement: Structured runtime logs classify common operational failures
The system SHALL classify common local runtime failures with structured fields that identify the component, error class, and relevant non-secret context.

#### Scenario: LLM throttling is structured
- **WHEN** an LLM request is rejected due to provider throttling or quota pressure
- **THEN** the log event identifies the component as LLM, the class as throttling, the provider/model context when available, and any active retry or backoff state without exposing secrets

#### Scenario: Network connection failures are structured
- **WHEN** a provider, Telegram, or tunnel request fails due to a connection error
- **THEN** the log event identifies the component, the class as network connectivity, and the endpoint category without logging API keys or token-bearing URLs

#### Scenario: cloudflared retry loops are structured
- **WHEN** cloudflared logs indicate repeated tunnel serve failures or retry loops
- **THEN** the observable signal identifies tunnel health as degraded rather than merely reporting that the process exists

### Requirement: High-volume expected logs are noise controlled
The system SHALL reduce high-volume expected logs so that local runtime logs remain useful for fault diagnosis.

#### Scenario: Polling 304 access logs do not dominate diagnostics
- **WHEN** repeated `check_status` requests return HTTP 304
- **THEN** runtime diagnostics are not dominated by one log line per poll cycle

#### Scenario: Noise control preserves failure visibility
- **WHEN** `check_status` returns an error, an unexpected status, or a state-changing response
- **THEN** the event remains visible in diagnostics despite any noise control applied to expected 304 responses

### Requirement: Existing log source compatibility is preserved
The system SHALL preserve existing local log file paths and system-log source names while adding structured fields and noise controls.

#### Scenario: Existing log readers continue to work
- **WHEN** an operator or system-logs MCP reads `api`, `web`, `cloudflared`, or `im_events`
- **THEN** the source name and backing file or table remain compatible with existing readers

#### Scenario: Structured additions are additive
- **WHEN** structured fields are added to runtime logs
- **THEN** existing plain-text log inspection still exposes a readable message and does not require a new log backend
