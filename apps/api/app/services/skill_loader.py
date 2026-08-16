"""Skill markdown loading for the agent loop.

Loads (name, markdown) for agent-bound skills and injects reference snippets
(export-report / improvement-habit) as soft context. Extracted from the deleted
task_policy.py so the runtime no longer depends on the deterministic policy layer.
"""

from __future__ import annotations

from pathlib import Path


def load_skill_mds(db, skill_ids: list[str]) -> list[tuple[str, str]]:
    """Load (name, markdown) for agent-bound skills; append key reference snippets."""
    from app.config import get_settings
    from app.models import Skill
    from app.services import skill_runtime

    out: list[tuple[str, str]] = []
    data_skills = Path(get_settings().data_dir) / "skills"
    for sid in skill_ids or []:
        sk = db.query(Skill).filter(Skill.id == sid).first()
        if not sk:
            continue
        try:
            md = skill_runtime.read_md(sk)
        except Exception:
            md = ""
        # Inject export-report / improvement-habit when present (domain harness)
        extra_bits: list[str] = []
        base = data_skills / str(sk.id)
        if base.is_dir():
            for rel, cap in (
                ("references/export-report.md", 2600),
                ("references/improvement-habit.md", 800),
            ):
                p = base / rel
                if not p.is_file():
                    continue
                try:
                    ref = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                body = ref[:cap]
                extra_bits.append(f"\n\n---\n# {rel}\n{body}")
        if extra_bits:
            md = (md or "") + "".join(extra_bits)
        out.append((sk.name or sid, md or ""))
    return out
