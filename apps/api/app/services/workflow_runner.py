import json
import logging

from sqlalchemy.orm import Session

from app.models import Agent, AgentGroup, GroupWorkflow
from app.security import now_str
from app.services.agent_runtime import run_agent, stop_chat
from app.services.group_message import parse_group_targets

logger = logging.getLogger(__name__)
_running: dict[str, bool] = {}


async def run_workflow(
    db: Session,
    group: AgentGroup,
    session_id: str,
    task: str,
    username: str,
    targets: list[dict] | None = None,
) -> dict:
    key = f"{group.id}:{session_id}"
    if _running.get(key):
        return {"status": "already_running"}
    _running[key] = True

    members = json.loads(group.members or "[]")
    mode, parsed = parse_group_targets(task, members, db)
    run_targets = targets if targets is not None else parsed
    if not run_targets and members:
        run_targets = [{**m, "_task": task} for m in members]
        mode = "all"

    wf = GroupWorkflow(
        group_id=group.id,
        session_id=session_id,
        task=task,
        mode=mode,
        enabled=True,
        status="running",
        creator=username,
        created_at=now_str(),
    )
    db.add(wf)
    db.commit()

    results = []
    try:
        for m in run_targets:
            agent = db.query(Agent).filter(Agent.id == m.get("agent_id")).first()
            if not agent:
                continue
            sid = m.get("session_id") or session_id
            subtask = m.get("_task") or task
            msg = subtask if subtask.startswith("[群任务]") else f"[群任务] {subtask}"
            reply = await run_agent(db, agent, sid, msg, username)
            results.append({
                "agent_id": agent.id,
                "name": m.get("name") or agent.name,
                "reply": reply,
                "task": subtask,
            })
        wf.status = "done"
    except Exception as e:
        wf.status = "error"
        logger.exception("workflow failed")
        results.append({"error": str(e)})
    finally:
        _running[key] = False
    db.commit()
    return {"status": wf.status, "mode": mode, "results": results}


def stop_workflow(group_id: str, session_id: str, db: Session):
    key = f"{group_id}:{session_id}"
    _running[key] = False
    group = db.query(AgentGroup).filter(AgentGroup.id == group_id).first()
    if not group:
        return
    for m in json.loads(group.members or "[]"):
        stop_chat(m.get("agent_id", ""), m.get("session_id") or session_id)
    wfs = db.query(GroupWorkflow).filter(
        GroupWorkflow.group_id == group_id,
        GroupWorkflow.session_id == session_id,
        GroupWorkflow.status == "running",
    ).all()
    for w in wfs:
        w.status = "stopped"
    db.commit()
