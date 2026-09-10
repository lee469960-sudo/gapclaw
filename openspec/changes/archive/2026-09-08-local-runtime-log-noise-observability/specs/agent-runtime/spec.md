## ADDED Requirements

### Requirement: No-progress hints are aggregated for diagnostics
The runtime MUST preserve the diagnostic value of repeated no-progress conditions while preventing one log line per loop iteration from dominating local runtime logs.

#### Scenario: Repeated no-progress hints are aggregated
- **WHEN** the same agent remains in a no-progress streak across repeated loop iterations
- **THEN** diagnostics expose the agent identifier, current iteration, streak length, and aggregate count or interval without emitting an unbounded identical log line per iteration

#### Scenario: Progress resets no-progress aggregation
- **WHEN** the runtime observes progress after a no-progress streak
- **THEN** the next no-progress diagnostic starts a new aggregate window rather than continuing the previous streak as if it were uninterrupted

#### Scenario: Aggregated hints remain operator-visible
- **WHEN** an operator reviews local runtime logs or log analysis output
- **THEN** they can still identify which agent is stuck and how long the no-progress streak has lasted
