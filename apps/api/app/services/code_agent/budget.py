"""Observable frozen budget consumption for Code runs."""

from __future__ import annotations

import json


def update_budget_usage(db, run, **facts) -> dict:
    try:
        usage = json.loads(getattr(run, "budget_usage", "") or "{}")
    except json.JSONDecodeError:
        usage = {}
    usage.update(facts)
    run.budget_usage = json.dumps(usage, sort_keys=True)
    db.commit()
    return usage
