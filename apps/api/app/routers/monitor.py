import platform
import shutil
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_session_user
from app.models import User, Agent, Sandbox
from app.schemas import ok
from app.services import docker_service
from app.services.agent_runtime import _running

router = APIRouter(prefix="/pages/page_monitor.cgi", tags=["monitor"])


@router.get("")
@router.post("")
async def monitor_handler(user: User = Depends(get_session_user), db: Session = Depends(get_db)):
    disk = shutil.disk_usage("/")
    docker = docker_service.docker_info()
    running_agents = len(_running)
    active_sandboxes = db.query(Sandbox).filter(Sandbox.status == "running").count()
    total_agents = db.query(Agent).count()
    return ok({
        "hostname": platform.node(),
        "platform": platform.platform(),
        "cpu_count": docker.get("cpu_count", 0),
        "memory_mb": docker.get("memory_mb", 0),
        "disk_total_gb": round(disk.total / (1024**3), 2),
        "disk_used_gb": round(disk.used / (1024**3), 2),
        "docker_connected": docker.get("connected", False),
        "running_agents": running_agents,
        "active_sandboxes": active_sandboxes,
        "total_agents": total_agents,
        "chat_jobs": running_agents,
        "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC"),
    })
