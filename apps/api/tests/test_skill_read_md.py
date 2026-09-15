import asyncio
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import get_settings
from app.database import Base
from app.models import Skill
from app.services import skill_runtime
from app.services.agent_tools import execute_action


def test_resolve_bound_skill_matches_id_name_and_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    root = tmp_path / "skills" / "e05b2cf5" / "ads-sync-hub"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("# hub\n", encoding="utf-8")
    skill = SimpleNamespace(id="e05b2cf5", name="Ads Sync Hub", zip_path="dummy.zip")
    other = SimpleNamespace(id="other", name="tushare-data", zip_path="other.zip")

    assert skill_runtime.resolve_bound_skill([skill], "e05b2cf5") is skill
    assert skill_runtime.resolve_bound_skill([skill], "Ads Sync Hub") is skill
    assert skill_runtime.resolve_bound_skill([skill], "ads-sync-hub") is skill
    assert skill_runtime.resolve_bound_skill([skill], "ADS-SYNC-HUB") is skill
    assert skill_runtime.resolve_bound_skill([skill], "unbound") is None
    assert skill_runtime.resolve_bound_skill([other], "ads-sync-hub") is None
    get_settings.cache_clear()


def test_read_md_lists_reference_paths_without_inlining_them(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    root = tmp_path / "skills" / "e05b2cf5" / "ads-sync-hub"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text("# Hub\nRead the SOP.", encoding="utf-8")
    (root / "references" / "sync-sop.md").write_text("SECRET_SOP_BODY", encoding="utf-8")
    (root / "references" / "report-sop.md").write_text("SECRET_REPORT_BODY", encoding="utf-8")
    skill = SimpleNamespace(id="e05b2cf5", name="ads-sync-hub", zip_path="dummy.zip")

    text = skill_runtime.read_md(skill)
    assert "# Hub" in text
    assert "references/sync-sop.md" in text
    assert "references/report-sop.md" in text
    assert "SKILL_MD: ads-sync-hub <path>" in text
    assert "SECRET_SOP_BODY" not in text
    assert "SECRET_REPORT_BODY" not in text
    get_settings.cache_clear()


def test_read_md_opens_package_reference_docs(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    root = tmp_path / "skills" / "e05b2cf5" / "ads-sync-hub"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text("# Hub\nSee references/report-sop.md", encoding="utf-8")
    (root / "references" / "report-sop.md").write_text("# Report SOP\n四要素", encoding="utf-8")
    skill = SimpleNamespace(id="e05b2cf5", name="ads-sync-hub", zip_path="dummy.zip")

    by_pkg = skill_runtime.read_md(skill, "references/report-sop.md")
    by_listed = skill_runtime.read_md(skill, "ads-sync-hub/references/report-sop.md")
    by_name = skill_runtime.read_md(skill, "report-sop.md")
    missing = skill_runtime.read_md(skill, "references/missing.md")
    escaped = skill_runtime.read_md(skill, "../../etc/passwd")

    assert "# Report SOP" in by_pkg
    assert by_listed == by_pkg
    assert by_name == by_pkg
    assert missing == "skill document not found"
    assert escaped == "skill document not found"
    get_settings.cache_clear()


def test_skill_read_md_action_resolves_bound_name(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    root = tmp_path / "skills" / "e05b2cf5" / "ads-sync-hub"
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text("# ads-sync-hub\n", encoding="utf-8")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Skill(id="e05b2cf5", name="ads-sync-hub", zip_path="dummy.zip"))
    db.commit()

    found = asyncio.run(execute_action(
        "skill_read_md", "SKILL_MD: ads-sync-hub",
        db, SimpleNamespace(), None, ["e05b2cf5"], [], [],
    ))
    by_id = asyncio.run(execute_action(
        "skill_read_md", "SKILL_MD: e05b2cf5",
        db, SimpleNamespace(), None, ["e05b2cf5"], [], [],
    ))
    missing = asyncio.run(execute_action(
        "skill_read_md", "SKILL_MD: ads-sync-hub",
        db, SimpleNamespace(), None, ["zzzzzzzz"], [], [],
    ))

    assert "# ads-sync-hub" in found
    assert "# ads-sync-hub" in by_id
    assert missing == "skill not found"
    get_settings.cache_clear()


def test_skill_read_md_action_reads_reference_path(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    root = tmp_path / "skills" / "e05b2cf5" / "ads-sync-hub"
    (root / "references").mkdir(parents=True)
    (root / "SKILL.md").write_text("# ads-sync-hub\n", encoding="utf-8")
    (root / "references" / "report-sop.md").write_text("# Report SOP\nexport rules", encoding="utf-8")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Skill(id="e05b2cf5", name="ads-sync-hub", zip_path="dummy.zip"))
    db.commit()

    spaced = asyncio.run(execute_action(
        "skill_read_md", "SKILL_MD: ads-sync-hub references/report-sop.md",
        db, SimpleNamespace(), None, ["e05b2cf5"], [], [],
    ))
    slashed = asyncio.run(execute_action(
        "skill_read_md", "SKILL_MD: ads-sync-hub/references/report-sop.md",
        db, SimpleNamespace(), None, ["e05b2cf5"], [], [],
    ))
    named = asyncio.run(execute_action(
        "skill_read_md", "SKILL_MD: ads-sync-hub report-sop.md",
        db, SimpleNamespace(), None, ["e05b2cf5"], [], [],
    ))

    assert "# Report SOP" in spaced
    assert spaced == slashed == named
    get_settings.cache_clear()
