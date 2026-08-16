"""Idempotent demo seed: MinMax LLM + dba sandbox + dba Agent + tushare MCP/Skill + system-logs."""

import json
import logging
import os
import sys
import zipfile
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Agent, LLMResource, MCP, Sandbox, Skill, SqlServer
from app.security import encrypt_secret, new_id, now_str
from app.services.llm_client import normalize_openai_base_url
from app.services.sql_service import parse_database_url

logger = logging.getLogger(__name__)

SEED_DIR = Path(__file__).resolve().parent.parent / "seed_assets"
API_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = API_DIR.parent.parent

DBA_PROMPT = """你是一名专业的数据库管理员（DBA）。收到用户问题后，认真思考，并通过工具组合解决/回答。
过程产物写入 task/<毫秒时间戳>/；仅最终交付文件写入当前目录（工作区根，例如 WRITE: dba_daily_checklist.md）。
路径已相对工作区根，禁止再创建或写入 workplace/ 子目录。
可使用 shell、读写工作区文件、SQL 工具查询数据库结构与数据。

【工具格式 - 必须严格遵守，禁止使用 XML/tool_call】
写文件：
WRITE: dba_daily_checklist.md
# 文档标题
正文...

完成后：
FINAL: 简要说明已保存的文件路径（当前目录下的文件名）"""

TUSHARE_PROMPT = """你是一名专业的 A 股与金融数据分析助手，绑定 tushareMcp 与 tushare-data Skill。
收到用户问题后，先理解意图（行情、财报、板块、资金流、宏观、导出等），再通过 MCP 工具获取真实数据并给出结论。

【数据获取 - 优先 MCP】
调用格式（严格遵守，禁止 XML/tool_call）：
MCP: trade_cal {"exchange":"SSE","start_date":"20250701","end_date":"20250710","is_open":"1"}
MCP: daily {"ts_code":"600519.SH","start_date":"20250601","end_date":"20250701"}
MCP: stock_basic {"list_status":"L","fields":["ts_code","name","industry"]}

常用工具：stock_basic、trade_cal、daily、daily_basic、fina_indicator、income、moneyflow、index_daily、news

【Skill 参考】
需要工作流指引时可读取：SKILL_MD: <tushare-data skill id>

【输出规范】
1. 一句话结论
2. 数据范围与口径（日期、代码格式 YYYYMMDD / 600519.SH）
3. 关键表格或指标
4. 权限/空数据等限制说明
5. 若用户需要导出：过程数据放 task/<时间戳>/，仅最终 CSV/报告写入当前目录（工作区根，禁止再套 workplace/）：
WRITE: tushare_report.md
# 标题
正文...

完成后：
FINAL: 简要说明结论；如有文件则说明路径"""

LOG_ANALYST_PROMPT = """你是 GAP 平台的系统日志分析助手，绑定 system-logs MCP 与 system-log-analyst Skill。
只通过 MCP 读取本机运行日志（.local/logs），基于真实日志归类错误并给出可执行优化建议。禁止臆造未读到的日志内容。

【工具格式 - 必须严格遵守】
MCP: list_log_sources {}
MCP: log_stats {"source":"api","minutes":60}
MCP: search_log {"source":"api","level":"ERROR","limit":80}
MCP: tail_log {"source":"api","lines":200,"grep":"Traceback"}

【Skill】
需要 SOP 时：SKILL_MD: <system-log-analyst skill id>

【输出】
使用 Markdown：日志诊断摘要表 + 关键发现 + 按优先级的优化建议 + 建议验证。
可选将完整报告写入工作区：WRITE: log_analysis_report.md
完成后 FINAL: 给出摘要与建议要点。"""

TUSHARE_ACTIONS = [
    "self_ask", "skill_read_md", "mcp_tool_call",
    "file_read", "file_write", "file_search_replace",
]

LOG_ANALYST_ACTIONS = [
    "self_ask", "skill_read_md", "mcp_tool_call",
    "file_read", "file_write",
]

DEFAULT_ACTIONS = [
    "self_ask", "skill_read_md", "skill_read_script", "skill_run_script",
    "mcp_tool_call", "httpmcp_call",
    "shell", "file_read", "file_write", "file_search", "file_search_replace",
]


def run_seed(db: Session, creator: str = "admin") -> dict:
    settings = get_settings()
    result = {"llm_id": None, "sandbox_id": None, "agent_id": None}

    llm = db.query(LLMResource).filter(LLMResource.name == "MinMax").first()
    if not llm and settings.minimax_api_key:
        llm = LLMResource(
            id=new_id(),
            type="llm",
            name="MinMax",
            provider="openai",
            base_url=normalize_openai_base_url(settings.minimax_base_url, "openai"),
            api_key_enc=encrypt_secret(settings.minimax_api_key),
            model=settings.minimax_model,
            description=f"MiniMax {settings.minimax_model}",
            visibility="private",
            creator=creator,
            modified_at=now_str(),
        )
        db.add(llm)
        db.commit()
        logger.info("seeded LLM MinMax id=%s", llm.id)
    if llm:
        result["llm_id"] = llm.id

    sb = db.query(Sandbox).filter(Sandbox.name == "dba-sandbox").first()
    if not sb:
        sb = Sandbox(
            id=new_id(),
            name="dba-sandbox",
            image=settings.default_sandbox_image,
            cpu_count=2,
            memory_mb=512,
            network_mode="bridge",
            visibility="private",
            status="stopped",
            creator=creator,
            created_at=now_str(),
            updated_at=now_str(),
        )
        db.add(sb)
        db.commit()
        from app.services.workplace import ensure_workplace
        ensure_workplace(sb.id)
        logger.info("seeded sandbox dba-sandbox id=%s", sb.id)
    result["sandbox_id"] = sb.id if sb else None

    if settings.seed_dba_agent:
        agent = db.query(Agent).filter(Agent.name == "dba").first()
        if not agent and llm and sb:
            aid = new_id()
            agent = Agent(
                id=aid,
                name="dba",
                description="数据库管理员 Agent，支持 SQL 查询与 workplace 文件管理",
                prompt=DBA_PROMPT,
                engine="react",
                llm_id=llm.id,
                sandbox_id=sb.id,
                skills=json.dumps([]),
                mcps=json.dumps([]),
                rags=json.dumps([]),
                proactivity=2,
                visibility="private",
                allowed_actions=json.dumps(DEFAULT_ACTIONS),
                creator=creator,
                created_at=now_str(),
                modified_at=now_str(),
                session_list=json.dumps([{"name": "default", "session_id": aid}]),
            )
            db.add(agent)
            db.commit()
            logger.info("seeded Agent dba id=%s", agent.id)
        if agent:
            result["agent_id"] = agent.id

    if settings.seed_tushare:
        tushare = _seed_tushare(db, creator, settings, llm=llm, sandbox=sb)
        result.update(tushare)

    if settings.seed_system_logs:
        logs = _seed_system_logs(db, creator, settings, llm=llm, sandbox=sb)
        result.update(logs)

    return result


def _absolute_sqlite_url(database_url: str) -> str:
    url = (database_url or "").strip()
    if not url.startswith("sqlite:///"):
        return url
    rest = url[len("sqlite:///") :]
    path = Path(rest)
    if not path.is_absolute():
        path = (API_DIR / rest).resolve()
    return f"sqlite:///{path}"


def _seed_skill_from_assets(db: Session, creator: str, settings, *, name: str, tags: str, description: str) -> Skill | None:
    skill = db.query(Skill).filter(Skill.name == name).first()
    if skill:
        return skill
    skill_dir = SEED_DIR / name
    if not (skill_dir.is_dir() and (skill_dir / "SKILL.md").is_file()):
        logger.warning("seed skill skip: missing %s", skill_dir)
        return None
    sid = new_id()
    zip_path = Path(settings.upload_dir) / "skills" / f"{sid}.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fp in skill_dir.rglob("*"):
            if fp.is_file():
                zf.write(fp, fp.relative_to(skill_dir.parent).as_posix())
    extract_dir = Path(settings.data_dir) / "skills" / sid
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)
    skill = Skill(
        id=sid,
        name=name,
        tags=tags,
        description=description,
        zip_path=str(zip_path),
        zip_name=f"{name}.zip",
        visibility="public",
        allowed_users="[]",
        creator=creator,
        modified_at=now_str(),
    )
    db.add(skill)
    db.commit()
    logger.info("seeded Skill %s id=%s", name, skill.id)
    return skill


def _seed_system_logs(db: Session, creator: str, settings, llm=None, sandbox=None) -> dict:
    out = {
        "system_logs_mcp_id": None,
        "system_log_skill_id": None,
        "system_log_agent_id": None,
    }
    log_dir = (REPO_ROOT / ".local" / "logs").resolve()
    log_dir.mkdir(parents=True, exist_ok=True)

    mcp = db.query(MCP).filter(MCP.name == "system-logs").first()
    python = sys.executable or "python3"
    args = ["-m", "mcp_servers.system_logs"]
    env = {
        "LOG_DIR": str(log_dir),
        "PYTHONPATH": os.pathsep.join(
            p for p in [str(API_DIR), os.environ.get("PYTHONPATH", "")] if p
        ),
        "DATABASE_URL": _absolute_sqlite_url(settings.database_url),
    }
    if not mcp:
        mcp = MCP(
            id=new_id(),
            name="system-logs",
            tags="运维,日志,ops",
            url="",
            headers="{}",
            protocol="stdio",
            command=python,
            command_args=json.dumps(args),
            command_env=json.dumps(env),
            description="只读读取 GAP 本地运行日志（.local/logs）与可选 ImEventLog，供日志分析 Agent 使用。",
            visibility="public",
            allowed_users="[]",
            creator=creator,
            modified_at=now_str(),
        )
        db.add(mcp)
        db.commit()
        logger.info("seeded MCP system-logs id=%s", mcp.id)
    else:
        # Keep command/env in sync with current checkout (idempotent refresh)
        mcp.protocol = "stdio"
        mcp.command = python
        mcp.command_args = json.dumps(args)
        mcp.command_env = json.dumps(env)
        mcp.modified_at = now_str()
        db.commit()
    out["system_logs_mcp_id"] = mcp.id

    skill = _seed_skill_from_assets(
        db,
        creator,
        settings,
        name="system-log-analyst",
        tags="运维,日志,分析",
        description="分析 GAP 本地运行日志并给出优化建议；配合 system-logs MCP。",
    )
    if skill:
        out["system_log_skill_id"] = skill.id

    if not llm:
        llm = db.query(LLMResource).filter(LLMResource.name == "MinMax").first()
    if not sandbox:
        sandbox = db.query(Sandbox).filter(Sandbox.name == "dba-sandbox").first()

    agent = db.query(Agent).filter(Agent.name == "log-analyst").first()
    mcp_ids = [mcp.id] if mcp else []
    skill_ids = [skill.id] if skill else []
    prompt = LOG_ANALYST_PROMPT.replace(
        "<system-log-analyst skill id>",
        skill.id if skill else "",
    )

    if not agent and llm and mcp:
        aid = new_id()
        agent = Agent(
            id=aid,
            name="log-analyst",
            description="系统日志分析 Agent：读取 .local/logs，归类错误并给出优化建议",
            prompt=prompt,
            engine="react",
            llm_id=llm.id,
            sandbox_id=sandbox.id if sandbox else "",
            skills=json.dumps(skill_ids),
            mcps=json.dumps(mcp_ids),
            rags=json.dumps([]),
            proactivity=2,
            visibility="public",
            allowed_actions=json.dumps(LOG_ANALYST_ACTIONS),
            creator=creator,
            created_at=now_str(),
            modified_at=now_str(),
            session_list=json.dumps([{"name": "default", "session_id": aid}]),
        )
        db.add(agent)
        db.commit()
        logger.info("seeded Agent log-analyst id=%s", agent.id)
    elif agent:
        changed = False
        current_mcps = json.loads(agent.mcps or "[]")
        if mcp and mcp.id not in current_mcps:
            current_mcps.append(mcp.id)
            agent.mcps = json.dumps(current_mcps)
            changed = True
        current_skills = json.loads(agent.skills or "[]")
        if skill and skill.id not in current_skills:
            current_skills.append(skill.id)
            agent.skills = json.dumps(current_skills)
            changed = True
        if not agent.llm_id and llm:
            agent.llm_id = llm.id
            changed = True
        if not agent.sandbox_id and sandbox:
            agent.sandbox_id = sandbox.id
            changed = True
        if "system-logs" in (agent.prompt or "") or "日志分析" in (agent.prompt or ""):
            if "<system-log-analyst skill id>" in (agent.prompt or "") and skill:
                agent.prompt = prompt
                changed = True
        if changed:
            agent.modified_at = now_str()
            db.commit()
            logger.info("updated Agent log-analyst id=%s", agent.id)

    if agent:
        out["system_log_agent_id"] = agent.id
    return out


def _seed_tushare(db: Session, creator: str, settings, llm=None, sandbox=None) -> dict:
    out = {"tushare_mcp_id": None, "tushare_skill_id": None, "tushare_agent_id": None}
    mcp_url = (settings.tushare_mcp_url or "").strip()
    if not mcp_url:
        return out

    mcp = db.query(MCP).filter(MCP.name == "tushareMcp").first()
    if not mcp:
        mcp = MCP(
            id=new_id(),
            name="tushareMcp",
            tags="金融,数据,A股",
            url=mcp_url,
            headers="{}",
            protocol="sse",
            description="Tushare 官方 MCP Server，提供 A 股、基金、指数、财务、宏观等 220+ 金融数据接口。",
            visibility="public",
            allowed_users="[]",
            creator=creator,
            modified_at=now_str(),
        )
        db.add(mcp)
        db.commit()
        logger.info("seeded MCP tushareMcp id=%s", mcp.id)
    out["tushare_mcp_id"] = mcp.id

    skill = db.query(Skill).filter(Skill.name == "tushare-data").first()
    if not skill:
        skill_dir = SEED_DIR / "tushare-data"
        if skill_dir.is_dir() and (skill_dir / "SKILL.md").is_file():
            sid = new_id()
            zip_path = Path(settings.upload_dir) / "skills" / f"{sid}.zip"
            zip_path.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for fp in skill_dir.rglob("*"):
                    if fp.is_file():
                        zf.write(fp, fp.relative_to(skill_dir.parent).as_posix())
            extract_dir = Path(settings.data_dir) / "skills" / sid
            extract_dir.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)
            skill = Skill(
                id=sid,
                name="tushare-data",
                tags="金融,数据,A股,Tushare",
                description="Tushare 金融数据研究 Skill，支持自然语言转数据查询、对比、导出流程。建议配合 tushareMcp 使用。",
                zip_path=str(zip_path),
                zip_name="tushare-data.zip",
                visibility="public",
                allowed_users="[]",
                creator=creator,
                modified_at=now_str(),
            )
            db.add(skill)
            db.commit()
            logger.info("seeded Skill tushare-data id=%s", skill.id)
    if skill:
        out["tushare_skill_id"] = skill.id

    if not llm:
        llm = db.query(LLMResource).filter(LLMResource.name == "MinMax").first()
    if not sandbox:
        sandbox = db.query(Sandbox).filter(Sandbox.name == "dba-sandbox").first()

    agent = db.query(Agent).filter(Agent.name == "tushare").first()
    mcp_ids = [mcp.id] if mcp else []
    skill_ids = [skill.id] if skill else []

    if not agent and llm and mcp:
        aid = new_id()
        agent = Agent(
            id=aid,
            name="tushare",
            description="Tushare 金融数据分析 Agent，默认绑定 tushareMcp，支持 A 股行情、财报、板块、资金流等查询",
            prompt=TUSHARE_PROMPT.replace("<tushare-data skill id>", skill.id if skill else ""),
            engine="react",
            llm_id=llm.id,
            sandbox_id=sandbox.id if sandbox else "",
            skills=json.dumps(skill_ids),
            mcps=json.dumps(mcp_ids),
            rags=json.dumps([]),
            proactivity=2,
            visibility="public",
            allowed_actions=json.dumps(TUSHARE_ACTIONS),
            creator=creator,
            created_at=now_str(),
            modified_at=now_str(),
            session_list=json.dumps([{"name": "default", "session_id": aid}]),
        )
        db.add(agent)
        db.commit()
        logger.info("seeded Agent tushare id=%s mcps=%s", agent.id, mcp_ids)
    elif agent:
        changed = False
        current_mcps = json.loads(agent.mcps or "[]")
        if mcp and mcp.id not in current_mcps:
            current_mcps.append(mcp.id)
            agent.mcps = json.dumps(current_mcps)
            changed = True
        current_skills = json.loads(agent.skills or "[]")
        if skill and skill.id not in current_skills:
            current_skills.append(skill.id)
            agent.skills = json.dumps(current_skills)
            changed = True
        if not agent.llm_id and llm:
            agent.llm_id = llm.id
            changed = True
        if not agent.sandbox_id and sandbox:
            agent.sandbox_id = sandbox.id
            changed = True
        if changed:
            agent.modified_at = now_str()
            db.commit()
            logger.info("updated Agent tushare id=%s mcps=%s", agent.id, json.loads(agent.mcps or "[]"))

    if agent:
        out["tushare_agent_id"] = agent.id
    return out


SYSTEM_PG_SQL_NAME = "GAP 系统库 (PostgreSQL)"
LEGACY_SYSTEM_PG_SQL_NAMES = (
    "gClaw 系统库 (PostgreSQL)",
    "gclaw 系统库 (PostgreSQL)",
    "gClaw Clone 系统库 (PostgreSQL)",
)
SYSTEM_PG_SQL_REMARK = "平台自身 PostgreSQL 数据库，由系统自动注册"


def _system_pg_cfg(settings) -> dict:
    cfg = parse_database_url(settings.database_url)
    if cfg:
        return cfg
    return {
        "db_type": "postgresql",
        "host": settings.system_pg_host,
        "port": settings.system_pg_port,
        "username": settings.system_pg_user,
        "password": settings.system_pg_password,
        "database": settings.system_pg_database,
    }


def _find_system_sql_server(db: Session) -> SqlServer | None:
    found = db.query(SqlServer).filter(SqlServer.name == SYSTEM_PG_SQL_NAME).first()
    if found:
        return found
    for name in LEGACY_SYSTEM_PG_SQL_NAMES:
        found = db.query(SqlServer).filter(SqlServer.name == name).first()
        if found:
            return found
    return (
        db.query(SqlServer)
        .filter(SqlServer.remark == SYSTEM_PG_SQL_REMARK, SqlServer.db_type == "postgresql")
        .first()
    )


def seed_system_postgres(db: Session, creator: str = "admin") -> dict | None:
    """Register/update the platform Postgres entry for the SQL tool (idempotent)."""
    settings = get_settings()
    if not settings.seed_system_postgres:
        return None

    cfg = _system_pg_cfg(settings)
    existing = _find_system_sql_server(db)

    # Drop duplicate legacy system rows once the canonical one exists.
    if existing:
        for name in LEGACY_SYSTEM_PG_SQL_NAMES:
            if name == existing.name:
                continue
            for dup in db.query(SqlServer).filter(SqlServer.name == name).all():
                if dup.id != existing.id:
                    db.delete(dup)

        existing.name = SYSTEM_PG_SQL_NAME
        existing.host = cfg["host"]
        existing.port = cfg["port"]
        existing.db_type = "postgresql"
        existing.username = cfg["username"]
        existing.password_enc = encrypt_secret(cfg["password"])
        existing.database = cfg["database"]
        existing.remark = SYSTEM_PG_SQL_REMARK
        if not existing.query_limit:
            existing.query_limit = 100
        db.commit()
        logger.info("synced SQL server %s id=%s", SYSTEM_PG_SQL_NAME, existing.id)
        return {"sql_server_id": existing.id}

    s = SqlServer(
        id=new_id(),
        name=SYSTEM_PG_SQL_NAME,
        host=cfg["host"],
        port=cfg["port"],
        db_type="postgresql",
        username=cfg["username"],
        password_enc=encrypt_secret(cfg["password"]),
        database=cfg["database"],
        query_limit=100,
        connect_timeout=10,
        read_timeout=60,
        write_timeout=60,
        remark=SYSTEM_PG_SQL_REMARK,
        creator=creator,
    )
    db.add(s)
    db.commit()
    logger.info("seeded SQL server %s id=%s", SYSTEM_PG_SQL_NAME, s.id)
    return {"sql_server_id": s.id}
