import zipfile
from pathlib import Path

from app.config import get_settings
from app.models import Skill
from app.services import docker_service


def _skill_dir(skill: Skill) -> Path | None:
    settings = get_settings()
    if skill.zip_path:
        extracted = Path(settings.data_dir) / "skills" / skill.id
        if extracted.exists() and any(extracted.iterdir()):
            return extracted
        try:
            extracted.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(skill.zip_path, "r") as zf:
                zf.extractall(extracted)
            return extracted
        except Exception:
            pass
    return None


def read_md(skill: Skill) -> str:
    base = _skill_dir(skill)
    if not base:
        return "# Skill not found"
    for md in base.rglob("SKILL.md"):
        return md.read_text(encoding="utf-8", errors="replace")
    return "# SKILL.md not found"


def read_script(skill: Skill, rel_path: str) -> str:
    base = _skill_dir(skill)
    if not base:
        return "skill not extracted"
    target = (base / rel_path.lstrip("/")).resolve()
    if not str(target).startswith(str(base.resolve())):
        return "invalid path"
    if target.is_file():
        return target.read_text(encoding="utf-8", errors="replace")[:8000]
    return "file not found"


def list_scripts(skill: Skill) -> list[str]:
    base = _skill_dir(skill)
    if not base:
        return []
    return [str(p.relative_to(base)) for p in base.rglob("*") if p.is_file()]


def run_script(
    skill: Skill,
    sandbox_id: str,
    command: str,
    container_id: str = "",
    timeout: int | None = None,
) -> str:
    base = _skill_dir(skill)
    if not base:
        return "skill not available"
    exec_timeout = int(timeout) if timeout else 1800
    if exec_timeout < 1:
        exec_timeout = 1800
    if container_id:
        cmd = command or f"ls -la /skills/{skill.id} 2>/dev/null || ls /skills"
        return docker_service.exec_in_sandbox(container_id, cmd, timeout=exec_timeout)
    for sh in base.rglob("*.sh"):
        return f"Script {sh.name} found. Start sandbox to execute: bash {sh.name}"
    return f"Skill {skill.name} ready; use sandbox to run scripts"
