"""Engine outcome contract for verifier-gated export finalization."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EngineOutcome:
    verifier_status: str = ""
    repair_status: str = ""
    can_claim_complete: bool = False
    can_deliver: bool = False
    should_block_final: bool = False
    blocking_reasons: list[str] = field(default_factory=list)
    repair_hints: list[str] = field(default_factory=list)
    verifier_result: dict[str, Any] = field(default_factory=dict)
    repair_plan: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verifier_status": self.verifier_status,
            "repair_status": self.repair_status,
            "can_claim_complete": self.can_claim_complete,
            "can_deliver": self.can_deliver,
            "should_block_final": self.should_block_final,
            "blocking_reasons": list(self.blocking_reasons),
            "repair_hints": list(self.repair_hints),
            "verifier_result": dict(self.verifier_result),
            "repair_plan": dict(self.repair_plan),
        }


def build_engine_outcome(
    verifier_result: dict[str, Any] | None,
    repair_plan: dict[str, Any] | None = None,
    *,
    mode: str = "analyzed",
    prefer_fallback: bool = False,
) -> EngineOutcome:
    """Normalize verifier + repair plan into FINAL gate decisions."""
    vr = verifier_result if isinstance(verifier_result, dict) else {}
    repair = repair_plan if isinstance(repair_plan, dict) else {}
    status = str(vr.get("status") or "").strip().lower()
    repair_status = str(repair.get("status") or "").strip().lower()
    is_fallback = mode == "fallback"
    should_block = bool(status == "failed" and not is_fallback and not prefer_fallback)
    reasons = [
        str(x)
        for x in (vr.get("blocking_reasons") or vr.get("errors") or [])
        if str(x).strip()
    ]
    hints = [
        str(x)
        for x in (vr.get("repair_hints") or repair.get("repair_hints") or [])
        if str(x).strip()
    ]
    return EngineOutcome(
        verifier_status=status,
        repair_status=repair_status,
        can_claim_complete=status == "pass",
        can_deliver=status in ("pass", "repairable") or bool(vr.get("summary") and not is_fallback),
        should_block_final=should_block,
        blocking_reasons=list(dict.fromkeys(reasons)),
        repair_hints=list(dict.fromkeys(hints)),
        verifier_result=dict(vr),
        repair_plan=dict(repair),
    )
