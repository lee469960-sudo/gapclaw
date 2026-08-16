from fastapi import APIRouter, Depends, Query

from app.deps import get_session_user
from app.models import User
from app.schemas import ok, fail
from app.services import docker_service

router = APIRouter(prefix="/pages/page_docker.cgi", tags=["docker"])


@router.get("")
@router.post("")
async def docker_handler(
    action: str = Query(None),
    id: str = Query(None),
    user: User = Depends(get_session_user),
):
    if action == "container_logs" and id:
        logs = docker_service.get_container_logs(id, tail=200)
        return ok({"logs": logs})

    info = docker_service.docker_info()
    images = docker_service.list_images()
    containers = docker_service.list_containers()
    return ok({"info": info, "images": images, "containers": containers})
