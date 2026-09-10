## Context

Current local diagnostics rely on `.local/logs/api.log`, `.local/logs/web.log`, `.local/logs/cloudflared.log`, and the `im_events` system-log source backed by the `im_event_logs` table. Recent triage showed useful signals, but the logs are noisy and ambiguous: `check_status` 304 polling dominates the API log, `no_progress_hint` repeats across loop iterations, Telegram poll failures produce repeated tracebacks, and LLM 429/connectivity failures lack enough structured context for trend analysis.

The implementation should preserve the existing log files and system-logs MCP source names. No new external logging backend is required.

## Goals / Non-Goals

**Goals:**

- Keep current local log paths and plain-text readability.
- Add structured, grep-friendly fields for common operational failures.
- Reduce expected high-volume noise without hiding unexpected errors.
- Make recent-window analysis more reliable for Telegram, LLM, cloudflared, polling, and Agent loop progress issues.
- Keep diagnostics compatible with `scripts/log-triage.sh`, `scripts/healthcheck-local.sh`, and the system-logs MCP.

**Non-Goals:**

- Do not add a metrics database, tracing stack, or external observability service.
- Do not change Agent protocol semantics, CodeAgent workspace behavior, or IM attachment handling.
- Do not remove full tracebacks for first occurrence or genuinely new failure classes.
- Do not treat LLM 429 as a business-code failure; expose it as provider throttling/degradation.

## Decisions

### Decision: Keep plain-text logs with structured key-value fields

Use existing Python logging and append stable key-value fields to messages, for example `component=llm class=throttling provider=minimax status=429 backoff_s=2 attempt=1`.

Rationale: this keeps current files, shell triage, and system-logs MCP compatible while giving scripts and humans better tokens to count.

Alternative considered: JSON logs. Rejected for this change because it would force updates to existing log readers and make current manual inspection less direct.

### Decision: Filter or sample expected `check_status` 304 access logs at the logging layer

Add a focused access-log filter for the high-volume `page_agent_chat.cgi?action=check_status` 304 path. Non-304 statuses, non-check-status requests, and errors remain visible.

Rationale: this is the dominant expected noise and can be handled before it reaches `api.log`.

Alternative considered: frontend polling changes. Rejected for this change because the goal is observability/log hygiene, not changing client behavior.

### Decision: Aggregate `no_progress_hint` per agent streak

Replace per-trigger repeated info logs with aggregate logs that include `agent`, `iter`, `streak`, and either a count or interval. Reset aggregation when progress resumes.

Rationale: operators still need to know an agent is stuck, but one line per repeated condition makes triage worse.

Alternative considered: removing the log entirely. Rejected because no-progress diagnostics are important when debugging long-running Agent tasks.

### Decision: Track Telegram poll failure streaks in memory

Maintain lightweight per-channel failure state in the poller process: last failure class, count, first/last timestamps, and recovery. Log full exception detail on the first failure in a streak or when the class changes, then emit compact aggregate lines for repeats. Emit a recovery line on the first successful poll after failures.

Rationale: this reduces repeated tracebacks while preserving enough detail to debug new or changing failures.

Alternative considered: persisting failure streaks to DB. Rejected for the first implementation because the poller loop is process-local and diagnostics do not require durable streak state.

### Decision: Make LLM 429/backoff observable at the retry boundary

Extend existing retry logging around HTTP 429/529 and transport errors with stable fields: component, class, provider, model, status, attempt, max_attempts, backoff, and sanitized endpoint category. Keep user-facing error messages unchanged except where they already report provider throttling.

Rationale: LLM 429 currently needs to be separated from application bugs and connected to retry/degradation behavior.

Alternative considered: changing model routing or provider concurrency in the same change. Rejected because that would expand scope beyond log observability.

### Decision: Keep scripts and system-logs MCP as consumers, not the source of truth

The application logs should become more structured; `scripts/log-triage.sh` and system-logs MCP can then count better tokens. The scripts should not become the only place that understands operational classes.

Rationale: diagnostics should work both through shell scripts and through the log-analyst Agent.

## Risks / Trade-offs

- Filtering access logs could hide useful request history -> Only filter expected 304 check-status responses; preserve non-304 and error cases.
- In-memory aggregation loses streak state on restart -> Acceptable for local diagnostics; post-restart logs still show fresh failures.
- Compact repeated Telegram logs may omit stack traces during a long outage -> Emit full exception on first occurrence and on failure-class changes.
- Structured key names could drift across components -> Define a small shared convention in implementation and cover it with focused tests.
- LLM provider names or endpoints may contain sensitive data -> Log provider/model identifiers only from non-secret resource metadata and sanitize endpoint values.

## Migration Plan

1. Add logging filter/formatter helpers without changing log file destinations.
2. Add focused unit tests for access-log filtering, no-progress aggregation, Telegram failure streak logging, and LLM retry fields.
3. Update `scripts/log-triage.sh` only if field names materially improve counters.
4. Update docs after implementation with the final structured field names and examples.
5. Rollback by disabling the new access-log filter and reverting aggregation helpers; existing log files remain readable.
