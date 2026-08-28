"""Skill markdown loading for the agent loop."""


def load_skill_mds(db, skill_ids: list[str]) -> list[tuple[str, str]]:
    """Load (name, markdown) for agent-bound skills."""
    from app.models import Skill
    from app.services import skill_runtime

    out: list[tuple[str, str]] = []
    for sid in skill_ids or []:
        sk = db.query(Skill).filter(Skill.id == sid).first()
        if not sk:
            continue
        try:
            md = skill_runtime.read_md(sk)
        except Exception:
            md = ""
        out.append((sk.name or sid, md or ""))
    return out
