import json
import asyncio
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.deps import get_session_user, resolve_ws_user
from app.models import User, Agent, Sandbox, SshServer
from app.services.agent_runtime import hub
from app.services import docker_service
from app.services.ssh_service import _connect

router = APIRouter(tags=["ws"])


@router.websocket("/pages/page_agent_chat.ws")
async def agent_chat_ws(websocket: WebSocket):
    await websocket.accept()
    key = None

    async def on_event(event):
        try:
            await websocket.send_json(event)
        except Exception:
            pass

    try:
        init = await websocket.receive_text()
        data = json.loads(init)
        db = SessionLocal()
        try:
            user = resolve_ws_user(data, db, websocket.cookies.get("session_id"))
            if not user and not websocket.cookies.get("session_id"):
                await websocket.send_json({"type": "error", "content": "未授权"})
                return
        finally:
            db.close()
        agent_id = data.get("agent_id", "")
        session_id = data.get("session_id", "")
        key = f"{agent_id}:{session_id}"
        hub.subscribe(key, on_event)
        await websocket.send_json({"type": "connected", "key": key})
        while True:
            msg = await websocket.receive_text()
            if msg == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        if key:
            hub.unsubscribe(key, on_event)


@router.websocket("/pages/page_sandbox.ws")
async def sandbox_ws(websocket: WebSocket):
    await websocket.accept()
    sock = None
    exec_id = None
    client = None
    try:
        init = json.loads(await websocket.receive_text())
        sandbox_id = init.get("id", "")
        shell = init.get("shell", "/bin/bash")
        workdir = init.get("workdir", "/workplace")
        db = SessionLocal()
        try:
            sb = db.query(Sandbox).filter(Sandbox.id == sandbox_id).first()
            if not sb or not sb.container_id:
                await websocket.send_json({"type": "error", "content": "沙箱未运行，请先点击「更多 → 启动」"})
                while True:
                    await websocket.receive_text()
                return
            status = docker_service.sync_container_status(sb.container_id)
            if status != "running":
                await websocket.send_json({"type": "error", "content": f"容器状态为 {status}，请重新启动沙箱"})
                return
            sock, exec_id, client = docker_service.attach_shell(sb.container_id, shell=shell, workdir=workdir)
            if not sock:
                await websocket.send_json({"type": "error", "content": "PTY 连接失败"})
                return
            await websocket.send_json({"type": "status", "content": "已连接", "workdir": workdir})
            cols = int(init.get("cols") or 80)
            rows = int(init.get("rows") or 24)
            docker_service.resize_shell(client, exec_id, cols, rows)
            read_task = asyncio.create_task(_relay_docker(sock, websocket))

            while True:
                data = await websocket.receive()
                if read_task.done():
                    break
                if "bytes" in data:
                    sock._sock.send(data["bytes"])
                elif "text" in data:
                    resize = _is_resize_message(data["text"])
                    if resize:
                        docker_service.resize_shell(
                            client, exec_id, int(resize["cols"]), int(resize["rows"])
                        )
                        continue
                    sock._sock.send(data["text"].encode("utf-8"))
        finally:
            db.close()
    except WebSocketDisconnect:
        pass
    finally:
        if "read_task" in locals() and not read_task.done():
            read_task.cancel()
        if sock:
            try:
                sock.close()
            except Exception:
                pass


async def _relay_docker(sock, websocket):
    loop = asyncio.get_running_loop()
    try:
        while True:
            data = await loop.run_in_executor(None, sock._sock.recv, 4096)
            if not data:
                break
            await websocket.send_bytes(data)
    except Exception:
        pass


def _is_resize_message(text: str) -> dict | None:
    if not text.startswith("{"):
        return None
    try:
        payload = json.loads(text)
    except Exception:
        return None
    if isinstance(payload, dict) and "cols" in payload and "rows" in payload:
        return payload
    return None


@router.websocket("/pages/page_terminal.ws")
async def terminal_ws(websocket: WebSocket):
    await websocket.accept()
    read_task = None
    client = None
    try:
        init = json.loads(await websocket.receive_text())
        server_id = init.get("id", "")
        db = SessionLocal()
        try:
            user = resolve_ws_user(init, db, websocket.cookies.get("session_id"))
            if not user:
                await websocket.send_json({"type": "error", "content": "未授权，请重新登录"})
                return
            srv = db.query(SshServer).filter(SshServer.id == server_id).first()
            if not srv:
                await websocket.send_json({"type": "error", "content": "服务器不存在"})
                return
            from app.deps import can_access_resource
            if not can_access_resource(user, srv.visibility, srv.allowed_users, srv.creator):
                await websocket.send_json({"type": "error", "content": "无权限访问该服务器"})
                return
            client = _connect(srv)
            channel = client.invoke_shell(term="xterm", width=init.get("cols", 80), height=init.get("rows", 24))
            channel.settimeout(0.0)
            await websocket.send_json({"type": "status", "content": f"SSH 已连接 · {srv.name}"})

            async def read_ssh():
                while True:
                    if channel.recv_ready():
                        data = channel.recv(4096)
                        if data:
                            await websocket.send_bytes(data)
                    await asyncio.sleep(0.05)

            read_task = asyncio.create_task(read_ssh())
            while True:
                data = await websocket.receive()
                if "bytes" in data:
                    channel.send(data["bytes"])
                elif "text" in data:
                    resize = _is_resize_message(data["text"])
                    if resize:
                        channel.resize_pty(int(resize["cols"]), int(resize["rows"]))
                        continue
                    channel.send(data["text"].encode())
        finally:
            db.close()
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_json({"type": "error", "content": str(e)})
        except Exception:
            pass
    finally:
        if read_task and not read_task.done():
            read_task.cancel()
        if client:
            try:
                client.close()
            except Exception:
                pass


@router.get("/pages/api_agent_cgi.cgi")
async def external_agent_api(agent_id: str, message: str, user: User = Depends(get_session_user)):
    from app.services.agent_runtime import run_agent
    db = SessionLocal()
    try:
        agent = db.query(Agent).filter(Agent.id == agent_id).first()
        if not agent:
            return {"code": 1, "msg": "Agent 不存在"}
        sid = json.loads(agent.session_list or "[]")[0]["session_id"] if agent.session_list else agent.id
        result = await run_agent(db, agent, sid, message, user.username)
        return {"code": 0, "msg": "ok", "data": {"reply": result}}
    finally:
        db.close()
