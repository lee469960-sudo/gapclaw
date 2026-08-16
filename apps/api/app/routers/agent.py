import json
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import can_access_resource, get_session_user
from app.models import User, Agent, AgentTick, LLMResource, Sandbox, Skill, MCP, RagCorpus
from app.schemas import ok, fail
from app.security import new_id, now_str

router = APIRouter(prefix="/pages/page_agent.cgi", tags=["agent"])


class AgentBody(BaseModel):
    action: str | None = None
    id: str | None = None
    name: str | None = None
    description: str = ""
    prompt: str = ""
    engine: str = "react"
    llm: str | None = None
    sandbox: str | None = None
    skills: list[str] | None = None
    mcps: list[str] | None = None
    rags: list[str] | None = None
    max_iterations: int = 150
    history_length: int = 30
    summary_max_words: int = 5000
    proactivity: int = 2
    llm_timeout: int = 1800
    skill_timeout: int = 1800
    shell_timeout: int = 1800
    mcp_soft_circuit: int = 5
    visibility: str = "private"
    allowed_users: list[str] | None = None
    allowed_actions: list[str] | None = None
    session_name: str | None = None
    session_id: str | None = None
    source_session_id: str | None = None
    content: str | None = None
    agent_id: str | None = None
    tick_id: str | None = None
    enabled: bool | None = None
    scope: str | None = None


DEFAULT_ACTIONS = [
    "self_ask", "skill_read_md", "skill_read_script", "skill_run_script",
    "mcp_tool_call", "httpmcp_call",
    "shell", "file_read", "file_write", "file_search", "file_search_replace",
]

KNOWN_ACTIONS = set(DEFAULT_ACTIONS) | {"rag_query"}


def _normalize_allowed_actions(actions: list[str] | None) -> list[str]:
    """Filter unknown keys and ignore `done` (engine always allows FINAL)."""
    if not actions:
        return list(DEFAULT_ACTIONS)
    cleaned: list[str] = []
    seen: set[str] = set()
    for key in actions:
        if key == "done" or key not in KNOWN_ACTIONS or key in seen:
            continue
        seen.add(key)
        cleaned.append(key)
    return cleaned or list(DEFAULT_ACTIONS)


def _filter_agents(items: list[Agent], user: User, scope: str = "all") -> list[Agent]:
    visible = [a for a in items if can_access_resource(user, a.visibility, a.allowed_users, a.creator)]
    if scope == "mine":
        return [a for a in visible if a.creator == user.username]
    return visible


def _agent_dict(a: Agent, db: Session) -> dict:
    d = a.to_dict()
    if a.llm_id:
        llm = db.query(LLMResource).filter(LLMResource.id == a.llm_id).first()
        d["llm_name"] = llm.name if llm else ""
    else:
        d["llm_name"] = ""
    if a.sandbox_id:
        sb = db.query(Sandbox).filter(Sandbox.id == a.sandbox_id).first()
        d["sandbox_name"] = sb.name if sb else ""
    else:
        d["sandbox_name"] = ""
    return d


def _agents_list(items: list[Agent], db: Session) -> list[dict]:
    llm_ids = {a.llm_id for a in items if a.llm_id}
    sb_ids = {a.sandbox_id for a in items if a.sandbox_id}
    llm_map = {
        r.id: r.name
        for r in db.query(LLMResource).filter(LLMResource.id.in_(llm_ids)).all()
    } if llm_ids else {}
    sb_map = {
        r.id: r.name
        for r in db.query(Sandbox).filter(Sandbox.id.in_(sb_ids)).all()
    } if sb_ids else {}
    result = []
    for a in items:
        d = a.to_dict()
        d["llm_name"] = llm_map.get(a.llm_id, "") if a.llm_id else ""
        d["sandbox_name"] = sb_map.get(a.sandbox_id, "") if a.sandbox_id else ""
        result.append(d)
    return result


def _agent_form_refs(db: Session, user: User) -> dict:
    """Resources for agent create/edit form; uses resource visibility, not page RBAC."""
    sandboxes = [
        {"id": s.id, "name": s.name}
        for s in db.query(Sandbox).all()
        if can_access_resource(user, s.visibility, s.allowed_users, s.creator)
    ]
    llms = [
        i.to_dict()
        for i in db.query(LLMResource).all()
        if can_access_resource(user, i.visibility, i.allowed_users, i.creator)
    ]
    skills = [
        s.to_dict()
        for s in db.query(Skill).all()
        if can_access_resource(user, s.visibility, s.allowed_users, s.creator)
    ]
    mcps = [
        m.to_dict()
        for m in db.query(MCP).all()
        if can_access_resource(user, m.visibility, m.allowed_users, m.creator)
    ]
    rags = [
        {"id": r.id, "name": r.name}
        for r in db.query(RagCorpus).all()
        if can_access_resource(user, r.visibility, r.allowed_users, r.creator)
    ]
    return {"sandboxes": sandboxes, "llms": llms, "skills": skills, "mcps": mcps, "rags": rags}


def _validate_refs(db: Session, body: AgentBody) -> str | None:
    if body.llm and not db.query(LLMResource).filter(LLMResource.id == body.llm).first():
        return f"LLM 不存在: {body.llm}"
    if body.sandbox and not db.query(Sandbox).filter(Sandbox.id == body.sandbox).first():
        return f"沙箱不存在: {body.sandbox}"
    if body.rags is not None:
        for rid in body.rags:
            if not db.query(RagCorpus).filter(RagCorpus.id == rid).first():
                return f"知识库不存在: {rid}"
    return None


@router.get("")
async def agent_get(
    action: str = Query("list"),
    id: str = Query(None),
    scope: str = Query("all"),
    user: User = Depends(get_session_user),
    db: Session = Depends(get_db),
):
    if action == "list":
        items = _filter_agents(db.query(Agent).all(), user, scope)
        return ok(_agents_list(items, db))
    if action == "refs":
        return ok(_agent_form_refs(db, user))
    if action == "get" and id:
        a = db.query(Agent).filter(Agent.id == id).first()
        if not a or not can_access_resource(user, a.visibility, a.allowed_users, a.creator):
            return fail("不存在或无权限")
        return ok(_agent_dict(a, db))
    return fail("未知操作")


@router.post("")
async def agent_post(body: AgentBody, user: User = Depends(get_session_user), db: Session = Depends(get_db)):
    action = body.action or "create"

    if action == "list":
        items = _filter_agents(db.query(Agent).all(), user, body.scope or "all")
        return ok(_agents_list(items, db))

    if action == "refs":
        return ok(_agent_form_refs(db, user))

    if action in ("create", "update"):
        err = _validate_refs(db, body)
        if err:
            return fail(err)
        if body.id:
            a = db.query(Agent).filter(Agent.id == body.id).first()
            if not a:
                return fail("不存在")
        else:
            sid = new_id()
            a = Agent(
                id=sid,
                creator=user.username,
                created_at=now_str(),
                session_list=json.dumps([{"name": "default", "session_id": sid}]),
            )
            db.add(a)
        a.name = body.name or a.name or "未命名"
        a.description = body.description
        a.prompt = body.prompt
        a.engine = "react"
        if body.llm:
            a.llm_id = body.llm
        if body.sandbox:
            a.sandbox_id = body.sandbox
        if body.skills is not None:
            a.skills = json.dumps(body.skills)
        if body.mcps is not None:
            a.mcps = json.dumps(body.mcps)
        if body.allowed_actions is not None:
            a.allowed_actions = json.dumps(_normalize_allowed_actions(body.allowed_actions))
        elif action == "create":
            a.allowed_actions = json.dumps(DEFAULT_ACTIONS)
        if body.rags is not None:
            a.rags = json.dumps(body.rags)
            if body.rags:
                actions = json.loads(a.allowed_actions or "[]")
                if "rag_query" not in actions:
                    actions.append("rag_query")
                    a.allowed_actions = json.dumps(actions)
        a.max_iterations = body.max_iterations
        a.history_length = body.history_length
        a.summary_max_words = body.summary_max_words
        a.proactivity = body.proactivity
        a.llm_timeout = body.llm_timeout
        a.skill_timeout = body.skill_timeout
        a.shell_timeout = body.shell_timeout
        a.mcp_soft_circuit = max(1, min(int(body.mcp_soft_circuit or 5), 50))
        a.visibility = body.visibility
        a.allowed_users = json.dumps(body.allowed_users or [])
        a.modified_at = now_str()
        db.commit()
        return ok(_agent_dict(a, db), "保存成功")

    if action == "delete":
        a = db.query(Agent).filter(Agent.id == body.id).first()
        if a:
            db.delete(a)
            db.commit()
        return ok(None, "删除成功")

    if action == "get_memory":
        a = db.query(Agent).filter(Agent.id == body.id).first()
        return ok({"content": a.memory if a else ""})

    if action == "save_memory":
        a = db.query(Agent).filter(Agent.id == body.id).first()
        if a:
            a.memory = body.content or ""
            db.commit()
        return ok(None, "保存成功")

    if action == "add_session":
        a = db.query(Agent).filter(Agent.id == body.id).first()
        if not a:
            return fail("不存在")
        sessions = json.loads(a.session_list or "[]")
        sid = body.session_id or new_id()
        sessions.append({"name": body.session_name or "新会话", "session_id": sid})
        a.session_list = json.dumps(sessions)
        db.commit()
        return ok(sessions, "创建成功")

    if action == "update_session":
        a = db.query(Agent).filter(Agent.id == body.id).first()
        if not a:
            return fail("不存在")
        sessions = json.loads(a.session_list or "[]")
        for s in sessions:
            if s["session_id"] == body.session_id:
                s["name"] = body.session_name or s["name"]
        a.session_list = json.dumps(sessions)
        db.commit()
        return ok(sessions, "修改成功")

    if action == "clone_session":
        a = db.query(Agent).filter(Agent.id == body.id).first()
        if not a:
            return fail("不存在")
        from app.models import ChatMessage, ChatNote, ChatSummary
        sessions = json.loads(a.session_list or "[]")
        new_sid = new_id()
        sessions.append({"name": body.session_name or "克隆会话", "session_id": new_sid})
        a.session_list = json.dumps(sessions)
        src = body.source_session_id or body.session_id
        if src:
            for m in db.query(ChatMessage).filter(ChatMessage.agent_id == a.id, ChatMessage.session_id == src).all():
                db.add(ChatMessage(agent_id=a.id, session_id=new_sid, role=m.role, content=m.content, meta=m.meta, created_at=m.created_at))
            note = db.query(ChatNote).filter(ChatNote.agent_id == a.id, ChatNote.session_id == src).first()
            if note:
                db.add(ChatNote(agent_id=a.id, session_id=new_sid, content=note.content))
            summ = db.query(ChatSummary).filter(ChatSummary.agent_id == a.id, ChatSummary.session_id == src).first()
            if summ:
                db.add(ChatSummary(agent_id=a.id, session_id=new_sid, content=summ.content))
        db.commit()
        return ok({"session_id": new_sid}, "克隆成功")

    if action == "delete_session":
        a = db.query(Agent).filter(Agent.id == body.id).first()
        if not a:
            return fail("不存在")
        sid = body.session_id
        from app.models import ChatMessage, ChatNote, ChatSummary
        from app.services import tick_scheduler
        db.query(ChatMessage).filter(ChatMessage.agent_id == a.id, ChatMessage.session_id == sid).delete()
        db.query(ChatNote).filter(ChatNote.agent_id == a.id, ChatNote.session_id == sid).delete()
        db.query(ChatSummary).filter(ChatSummary.agent_id == a.id, ChatSummary.session_id == sid).delete()
        ticks = db.query(AgentTick).filter(AgentTick.agent_id == a.id, AgentTick.session_id == sid).all()
        for t in ticks:
            tick_scheduler.remove_tick_job(t.tick_id)
        db.query(AgentTick).filter(AgentTick.agent_id == a.id, AgentTick.session_id == sid).delete()
        sessions = [s for s in json.loads(a.session_list or "[]") if s["session_id"] != sid]
        a.session_list = json.dumps(sessions)
        db.commit()
        return ok(sessions, "删除成功")

    if action == "list_ticks":
        ticks = db.query(AgentTick).filter(
            AgentTick.agent_id == (body.agent_id or body.id),
            AgentTick.session_id == body.session_id,
        ).all()
        return ok([{
            "tick_id": t.tick_id, "cron": t.cron, "message": t.message,
            "enabled": t.enabled, "creator": t.creator, "agent_id": t.agent_id,
        } for t in ticks])

    if action == "list_all_ticks":
        ticks = db.query(AgentTick).filter(AgentTick.creator == user.username).all()
        return ok([{
            "tick_id": t.tick_id, "cron": t.cron, "message": t.message,
            "enabled": t.enabled, "agent_id": t.agent_id, "session_id": t.session_id,
        } for t in ticks])

    if action == "toggle_tick":
        t = db.query(AgentTick).filter(AgentTick.tick_id == body.tick_id).first()
        if t:
            t.enabled = body.enabled if body.enabled is not None else not t.enabled
            db.commit()
        return ok(None, "操作成功")

    if action == "delete_tick":
        db.query(AgentTick).filter(AgentTick.tick_id == body.tick_id).delete()
        db.commit()
        return ok(None, "删除成功")

    return fail("未知操作")
