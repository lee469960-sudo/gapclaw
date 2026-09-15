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


def _skill_aliases(skill: Skill) -> list[str]:
    aliases = [str(skill.id or "").strip(), str(skill.name or "").strip()]
    base = _skill_dir(skill)
    if base is not None:
        aliases.append(base.name)
        aliases.extend(path.name for path in base.iterdir() if path.is_dir())
    seen: set[str] = set()
    unique: list[str] = []
    for alias in aliases:
        key = alias.casefold()
        if not alias or key in seen:
            continue
        seen.add(key)
        unique.append(alias)
    return unique


def resolve_bound_skill(skills: list[Skill], token: str) -> Skill | None:
    raw = str(token or "").strip().strip("`\"'")
    if not raw or not skills:
        return None
    folded = raw.casefold()
    for skill in skills:
        if str(skill.id or "") == raw or str(skill.name or "").casefold() == folded:
            return skill
    for skill in skills:
        aliases = {alias.casefold() for alias in _skill_aliases(skill)}
        if folded in aliases:
            return skill
    return None


def split_skill_md_request(skills: list[Skill], token: str) -> tuple[Skill | None, str]:
    raw = str(token or "").strip().strip("`\"'")
    if not raw:
        return None, ""
    skill = resolve_bound_skill(skills, raw)
    if skill:
        return skill, ""
    if any(sep in raw for sep in (" ", "\t")):
        head, tail = raw.split(None, 1)
        return resolve_bound_skill(skills, head), tail.strip().strip("`\"'")
    folded = raw.replace("\\", "/")
    for skill in skills:
        for alias in _skill_aliases(skill):
            prefix = f"{alias}/"
            if folded.startswith(prefix) or folded.casefold().startswith(prefix.casefold()):
                return skill, folded.split("/", 1)[1]
    if len(skills) == 1 and ("/" in folded or folded.casefold().endswith(".md")):
        return skills[0], folded
    return None, ""


def _package_root(base: Path) -> Path:
    skill_md = next(base.rglob("SKILL.md"), None)
    return skill_md.parent if skill_md else base


def _safe_join(root: Path, rel_path: str) -> Path | None:
    base = root.resolve()
    target = (root / rel_path.lstrip("/")).resolve()
    if target.is_file() and target.is_relative_to(base):
        return target
    return None


def _resolve_skill_doc(skill: Skill, rel_path: str) -> Path | None:
    base = _skill_dir(skill)
    if not base:
        return None
    raw = str(rel_path or "").strip().strip("`\"'").replace("\\", "/")
    if not raw:
        return next(base.rglob("SKILL.md"), None)
    roots = [base, _package_root(base)]
    seen: set[Path] = set()
    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        found = _safe_join(root, raw)
        if found:
            return found
    name = Path(raw).name.casefold()
    suffix = raw.lstrip("/").casefold()
    matches = [
        path for path in base.rglob("*")
        if path.is_file() and (
            path.name.casefold() == name
            or str(path.relative_to(base)).replace("\\", "/").casefold().endswith(suffix)
        )
    ]
    return matches[0] if len(matches) == 1 else None


def read_md(skill: Skill, rel_path: str = "") -> str:
    base = _skill_dir(skill)
    if not base:
        return "# Skill not found"
    if rel_path:
        doc = _resolve_skill_doc(skill, rel_path)
        if not doc:
            return "skill document not found"
        return doc.read_text(encoding="utf-8", errors="replace")
    skill_md = next(base.rglob("SKILL.md"), None)
    body = skill_md.read_text(encoding="utf-8", errors="replace") if skill_md else "# SKILL.md not found"
    package = _package_root(base)
    references: list[Path] = []
    for folder in base.rglob("references"):
        if folder.is_dir():
            references.extend(sorted(path for path in folder.glob("*.md") if path.is_file()))
    if not references:
        return body
    name = str(skill.name or skill.id)
    lines = [
        body.rstrip(),
        "",
        "## References",
        f"以下文档在 Skill 包内。读取用 `SKILL_MD: {name} <path>`，不要用 workplace 的 READ：",
    ]
    for path in references:
        try:
            listed = path.relative_to(package)
        except ValueError:
            listed = path.relative_to(base)
        lines.append(f"- {listed}")
    return "\n".join(lines)


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
