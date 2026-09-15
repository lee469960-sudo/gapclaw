import concurrent.futures
import logging
import os
import re
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path

from app.config import get_settings

logger = logging.getLogger(__name__)

_client = None
_client_failed = False
_exec_locks: dict[str, threading.Lock] = {}
_exec_locks_guard = threading.Lock()


def get_docker_client():
    global _client, _client_failed
    if _client_failed:
        return None
    if _client is not None:
        return _client
    try:
        import docker
        settings = get_settings()
        _client = docker.DockerClient(base_url=settings.docker_socket)
        return _client
    except Exception as e:
        logger.warning("Docker unavailable: %s", e)
        _client_failed = True
        return None


def _format_created(created_str: str) -> tuple[str, float]:
    if not created_str:
        return "", 0.0
    try:
        dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S"), dt.timestamp()
    except Exception:
        return created_str[:19].replace("T", " "), 0.0


def docker_info() -> dict:
    client = get_docker_client()
    if not client:
        return {"cpu_count": 0, "memory_mb": 0, "error": "Docker 不可用", "connected": False, "version": ""}
    try:
        info = client.info()
        return {
            "cpu_count": info.get("NCPU", 0),
            "memory_mb": info.get("MemTotal", 0) // (1024 * 1024),
            "error": None,
            "connected": True,
            "version": client.version().get("Version", ""),
        }
    except Exception as e:
        return {"cpu_count": 0, "memory_mb": 0, "error": str(e), "connected": False, "version": ""}


def list_images() -> list[dict]:
    client = get_docker_client()
    if not client:
        return []
    result = []
    for img in client.images.list():
        tags = img.tags or ["<none>"]
        tag = tags[0] if tags and tags[0] != "<none>" else ""
        created_raw = img.attrs.get("Created", "")
        created, created_ts = _format_created(created_raw)
        result.append({
            "id": img.short_id.replace("sha256:", "")[:12],
            "full_id": img.id,
            "tag": tag,
            "tags": tags,
            "size": f"{img.attrs.get('Size', 0) / (1024 * 1024):.1f} MB",
            "size_bytes": img.attrs.get("Size", 0),
            "created": created,
            "created_ts": created_ts,
        })
    result.sort(key=lambda x: x.get("created_ts", 0), reverse=True)
    for item in result:
        item.pop("created_ts", None)
    return result


def sync_containers_status(container_ids: list[str]) -> dict[str, str]:
    client = get_docker_client()
    if not client:
        return {}
    result: dict[str, str] = {}
    for cid in container_ids:
        if not cid:
            continue
        try:
            result[cid] = client.containers.get(cid).status
        except Exception:
            result[cid] = "stopped"
    return result


def sync_container_status(container_id: str) -> str | None:
    if not container_id:
        return None
    return sync_containers_status([container_id]).get(container_id)


def export_image(ref: str) -> Path | None:
    client = get_docker_client()
    if not client or not ref:
        return None
    try:
        img = client.images.get(ref)
        fd, path = tempfile.mkstemp(suffix=".tar")
        os.close(fd)
        with open(path, "wb") as f:
            for chunk in img.save(named=True):
                f.write(chunk)
        return Path(path)
    except Exception as e:
        logger.error("export_image failed: %s", e)
        return None


def import_image(data: bytes) -> list[str]:
    client = get_docker_client()
    if not client:
        return []
    try:
        loaded = client.images.load(data)
        tags: list[str] = []
        for img in loaded:
            tags.extend(img.tags or [])
        return tags
    except Exception as e:
        logger.error("import_image failed: %s", e)
        return []


def _host_path_for_container_mount(container_path: str) -> Path | None:
    """Resolve host source path for a bind mount visible inside this container."""
    mountinfo = Path("/proc/self/mountinfo")
    if not mountinfo.exists():
        return None
    target = (container_path or "/").rstrip("/") or "/"
    best_mount = ""
    best_host: Path | None = None
    for line in mountinfo.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split(" - ", 1)
        if len(parts) != 2:
            continue
        left = parts[0].split()
        right = parts[1].split()
        if len(left) < 5:
            continue
        root = left[3]
        mnt = left[4]
        mnt_norm = mnt.rstrip("/") or "/"
        target_norm = target.rstrip("/") or "/"
        if target_norm != mnt_norm and not target_norm.startswith(mnt_norm + "/"):
            continue

        host_path: Path | None = None
        if root.startswith("/") and not root.startswith("/dev/"):
            host_path = Path(root)
        elif len(right) >= 2 and right[1].startswith("/") and not right[1].startswith("/dev/"):
            host_path = Path(right[1])

        if host_path is None:
            continue
        if target_norm != mnt_norm:
            suffix = target_norm[len(mnt_norm) :].lstrip("/")
            host_path = host_path / suffix if suffix else host_path
        if len(mnt_norm) >= len(best_mount):
            best_mount = mnt_norm
            best_host = host_path
    return best_host


def _docker_bind_root() -> Path:
    """Host path for docker volume binds (differs from in-container data_dir)."""
    settings = get_settings()
    data_dir = Path(settings.data_dir).resolve()

    if settings.docker_data_host_path:
        configured = Path(settings.docker_data_host_path)
        if configured.is_absolute():
            return configured.resolve()

    detected = _host_path_for_container_mount(str(data_dir))
    if detected and detected.is_absolute():
        logger.info("auto-detected docker bind root from %s -> %s", data_dir, detected)
        return detected.resolve()

    if settings.docker_data_host_path:
        return Path(settings.docker_data_host_path).resolve()

    logger.warning(
        "DOCKER_DATA_HOST_PATH not set and auto-detect failed; "
        "sandbox /workplace may be empty (data_dir=%s)",
        data_dir,
    )
    return data_dir


def _workplace_mount_matches(container, expected_host_path: str) -> bool:
    expected = str(Path(expected_host_path).resolve())
    for mount in container.attrs.get("Mounts") or []:
        if mount.get("Destination") == "/workplace":
            source = mount.get("Source") or ""
            return str(Path(source).resolve()) == expected
    return False


def _image_exists(client, ref: str) -> bool:
    try:
        client.images.get(ref)
        return True
    except Exception:
        return False


def _resolve_local_image(ref: str) -> str | None:
    """Match a sandbox image ref to an existing local tag (incl. registry aliases)."""
    client = get_docker_client()
    if not client or not ref:
        return None
    ref = ref.strip()
    if _image_exists(client, ref):
        return ref

    short = ref.rsplit("/", 1)[-1]
    fallbacks: list[str] = []
    if short in ("myclaw-base-amd:latest", "myclaw-base-arm:latest"):
        fallbacks.extend(["myclaw-base:latest", short])
    elif short == "myclaw-base:latest":
        fallbacks.extend(["myclaw-base-amd:latest", "myclaw-base-arm:latest"])

    for alt in fallbacks:
        if alt != ref and _image_exists(client, alt):
            return alt

    for img in client.images.list():
        for tag in img.tags or []:
            if tag == ref or tag.endswith("/" + short) or tag == short:
                return tag
    return None


def _myclaw_runnable_dockerfile() -> Path | None:
    for candidate in (
        Path("/app/deploy/myclaw-runnable.Dockerfile"),
        Path(__file__).resolve().parents[4] / "deploy" / "myclaw-runnable.Dockerfile",
    ):
        if candidate.exists():
            return candidate
    return None


def _is_myclaw_sandbox_image(image: str) -> bool:
    name = (image or "").lower()
    return "myclaw" in name and "runnable" not in name


def _is_myclaw_family(image: str) -> bool:
    return "myclaw" in (image or "").lower()


def _format_docker_error(exc: Exception) -> str:
    parts: list[str] = []
    for attr in ("explanation", "message", "detail"):
        val = getattr(exc, attr, None)
        if val:
            parts.append(str(val))
    text = str(exc).strip()
    if text and text not in parts:
        parts.append(text)
    return parts[0] if len(parts) == 1 else "; ".join(parts) if parts else repr(exc)


def _runnable_registry_ref() -> str | None:
    settings = get_settings()
    registry = (settings.gap_registry or "").strip().rstrip("/")
    if not registry:
        return None
    namespace = (settings.gap_namespace or "tools_claw").strip()
    arch = (settings.gap_arch or "amd").strip()
    return f"{registry}/{namespace}/myclaw-base-runnable-{arch}:latest"


def ensure_myclaw_runnable(source_image: str = "myclaw-base:latest") -> str:
    """Use prebuilt myclaw-base-runnable, or build when myclaw-base has broken VOLUME metadata."""
    client = get_docker_client()
    source = _resolve_local_image(source_image) or (source_image or "myclaw-base:latest").strip()
    if not client:
        return source
    runnable = "myclaw-base-runnable:latest"
    if _image_exists(client, runnable):
        return runnable

    remote = _runnable_registry_ref()
    if remote:
        try:
            logger.info("pulling %s", remote)
            pulled = client.images.pull(remote)
            imgs = pulled if isinstance(pulled, list) else [pulled]
            for img in imgs:
                img.tag("myclaw-base-runnable", "latest")
            if _image_exists(client, runnable):
                return runnable
        except Exception as e:
            logger.warning("pull myclaw-base-runnable from registry failed: %s", e)

    if not _image_exists(client, source):
        hint = f"基础镜像 {source_image} 不在本地，请先在「镜像管理」拉取或上传"
        if remote:
            hint += f"；或拉取 {remote}"
        raise RuntimeError(hint)
    dockerfile = _myclaw_runnable_dockerfile()
    if not dockerfile:
        raise RuntimeError("缺少 myclaw-runnable.Dockerfile，无法修复镜像 VOLUME 冲突")
    try:
        logger.info("building %s from %s (base=%s)", runnable, dockerfile, source)
        result = client.images.build(
            path=str(dockerfile.parent),
            dockerfile=dockerfile.name,
            tag=runnable,
            pull=False,
            rm=True,
            decode=True,
            buildargs={"BASE_IMAGE": source},
        )
        for chunk in result:
            if isinstance(chunk, dict) and chunk.get("error"):
                raise RuntimeError(chunk["error"])
        if not _image_exists(client, runnable):
            raise RuntimeError("构建完成但未找到 myclaw-base-runnable:latest")
        return runnable
    except Exception as e:
        logger.error("build myclaw-base-runnable failed: %s", e)
        raise RuntimeError(f"构建 myclaw-base-runnable 失败: {_format_docker_error(e)}") from e


def resolve_sandbox_image(image: str) -> str:
    image = (image or "").strip()
    if _is_myclaw_sandbox_image(image):
        return ensure_myclaw_runnable(image)
    client = get_docker_client()
    local = _resolve_local_image(image) if client else None
    return local or image or "python:3.12-slim"


def _sandbox_volumes(sandbox, bind_root: Path, wp_bind: Path, run_image: str = "") -> dict:
    """Build bind mounts using host paths for docker.sock."""
    if _is_myclaw_family(sandbox.image) or _is_myclaw_family(run_image):
        return {str(wp_bind): {"bind": "/workplace", "mode": "rw"}}
    return {
        str(bind_root / "skills"): {"bind": "/skills", "mode": "ro"},
        str(bind_root / "engine"): {"bind": "/engine", "mode": "ro"},
        str(bind_root / "mcp"): {"bind": "/mcp", "mode": "ro"},
        str(bind_root / "llm"): {"bind": "/llm", "mode": "ro"},
        str(wp_bind): {"bind": "/workplace", "mode": "rw"},
    }


def start_sandbox(sandbox) -> tuple[str, str, str]:
    client = get_docker_client()
    settings = get_settings()
    data_dir = Path(settings.data_dir).resolve()
    bind_root = _docker_bind_root()
    wp_container = Path(settings.workplace_dir).resolve() / sandbox.id / "workplace"
    wp_bind = bind_root / "workplaces" / sandbox.id / "workplace"
    wp_container.mkdir(parents=True, exist_ok=True)
    for sub in ["skills", "engine", "mcp", "llm"]:
        (data_dir / sub).mkdir(parents=True, exist_ok=True)

    if not client:
        return "", "error", "Docker 不可用，请确认 Docker Desktop 已启动"

    name = f"gap-sandbox-{sandbox.id}"
    try:
        run_image = resolve_sandbox_image(sandbox.image)
    except RuntimeError as e:
        return "", "error", str(e)
    actual_image = _resolve_local_image(run_image) or (run_image if _image_exists(client, run_image) else None)
    if not actual_image:
        return (
            "",
            "error",
            f"镜像 {sandbox.image or run_image} 不在本地，请先在「镜像管理」拉取或上传",
        )
    volumes = _sandbox_volumes(sandbox, bind_root, wp_bind, actual_image)
    expected_wp = str(wp_bind.resolve())
    logger.info(
        "sandbox %s workplace bind: host=%s container=/workplace api=%s",
        sandbox.id,
        expected_wp,
        wp_container,
    )
    try:
        if sandbox.container_id:
            try:
                c = client.containers.get(sandbox.container_id)
                mount_ok = _workplace_mount_matches(c, expected_wp)
                if c.status == "running" and mount_ok:
                    return c.id, "running", ""
                if c.status != "running" and mount_ok:
                    c.start()
                    return c.id, "running", ""
                logger.warning(
                    "recreating sandbox %s container (status=%s mount_ok=%s expected=%s)",
                    sandbox.id,
                    c.status,
                    mount_ok,
                    expected_wp,
                )
                c.remove(force=True)
            except Exception:
                pass
        try:
            stale = client.containers.get(name)
            stale.remove(force=True)
        except Exception:
            pass
        cpu_count = max(1, int(sandbox.cpu_count or 1))
        container = client.containers.run(
            actual_image,
            command="sleep infinity",
            name=name,
            detach=True,
            volumes=volumes,
            working_dir="/workplace",
            hostname=f"sandbox-{sandbox.id[:8]}",
            mem_limit=f"{int(sandbox.memory_mb or 512)}m",
            cpu_period=100_000,
            cpu_quota=100_000 * cpu_count,
            network_mode=sandbox.network_mode or "bridge",
            tty=True,
        )
        return container.id, "running", ""
    except Exception as e:
        logger.error("start sandbox failed: %r", e)
        return "", "error", _format_docker_error(e)


def stop_sandbox(container_id: str) -> None:
    client = get_docker_client()
    if client and container_id:
        try:
            client.containers.get(container_id).stop(timeout=10)
        except Exception:
            pass


def destroy_sandbox(container_id: str, container_name: str) -> None:
    client = get_docker_client()
    if not client:
        return
    for cid in [container_id, container_name]:
        if not cid:
            continue
        try:
            c = client.containers.get(cid)
            c.stop(timeout=5)
            c.remove(force=True)
        except Exception:
            pass


_SHELL_STDOUT_MAX_BYTES = 128 * 1024  # 128KB hard cap for chat/tool UX
_EXEC_RECOVERY_WAIT_SECONDS = 3.0


def _truncate_exec_output(output) -> str:
    """Decode and hard-cap shell stdout to avoid multi-MB freezes."""
    if output is None:
        return ""
    if isinstance(output, bytes):
        raw = output
        total = len(raw)
        if total > _SHELL_STDOUT_MAX_BYTES:
            head = raw[:48 * 1024]
            tail = raw[-16 * 1024 :]
            text = (
                head.decode("utf-8", errors="replace")
                + f"\n…(已截断，原文约 {total} 字节，仅保留头尾)…\n"
                + tail.decode("utf-8", errors="replace")
            )
            return text
        return raw.decode("utf-8", errors="replace")
    text = str(output or "")
    # Approximate char cap ~ same as byte cap for ASCII-heavy dumps
    max_chars = _SHELL_STDOUT_MAX_BYTES
    if len(text) > max_chars:
        return (
            text[: 48 * 1024]
            + f"\n…(已截断，原文约 {len(text)} 字符，仅保留头尾)…\n"
            + text[-16 * 1024 :]
        )
    return text


def _exec_lock(container_id: str) -> threading.Lock:
    """Return the process-wide serialization lock for one sandbox container."""
    with _exec_locks_guard:
        return _exec_locks.setdefault(container_id, threading.Lock())


def _docker_status_code(exc: BaseException) -> int | None:
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def _container_status(container) -> str:
    container.reload()
    return str(getattr(container, "status", "") or "unknown").lower()


def _prepare_container_for_exec(container, *, wait_seconds: float) -> tuple[bool, str, str]:
    """Refresh and recover a sandbox state before a Docker exec.

    Sandbox tool execution implies that this persistent container should be
    runnable. Recover states Docker can safely reverse and bound any wait for a
    container that is already restarting.
    """
    try:
        status = _container_status(container)
        if status in {"created", "exited"}:
            container.start()
        elif status == "paused":
            container.unpause()

        deadline = time.monotonic() + max(0.0, wait_seconds)
        while status in {"created", "exited", "paused", "restarting"}:
            status = _container_status(container)
            if status == "running":
                return True, status, ""
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
        return status == "running", status, ""
    except Exception as exc:
        return False, "unknown", _format_docker_error(exc)


def exec_in_sandbox(container_id: str, command: str, timeout: int = 60) -> str:
    client = get_docker_client()
    if not client or not container_id:
        return f"[simulated] $ {command}\n(no docker)"
    timeout = int(timeout) if timeout else 60
    if timeout < 1:
        timeout = 60
    lock = _exec_lock(container_id)
    if not lock.acquire(timeout=timeout):
        return f"[sandbox_busy] 同一容器已有命令执行超过 {timeout}s，本次命令未执行。"
    try:
        c = client.containers.get(container_id)
        ready, status, prepare_error = _prepare_container_for_exec(
            c, wait_seconds=min(_EXEC_RECOVERY_WAIT_SECONDS, float(timeout)),
        )
        if not ready:
            detail = f"：{prepare_error}" if prepare_error else ""
            return f"[sandbox_unavailable] 容器状态为 {status}，无法执行命令{detail}"

        def _run():
            return c.exec_run(["/bin/sh", "-c", command], demux=False)

        for attempt in range(2):
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    fut = pool.submit(_run)
                    exit_code, output = fut.result(timeout=timeout)
                del exit_code  # retained for future status surfacing
                return _truncate_exec_output(output)
            except concurrent.futures.TimeoutError:
                return f"exec timeout after {timeout}s"
            except Exception as exc:
                if _docker_status_code(exc) != 409:
                    return f"exec error: {exc}"
                ready, status, prepare_error = _prepare_container_for_exec(
                    c, wait_seconds=min(_EXEC_RECOVERY_WAIT_SECONDS, float(timeout)),
                )
                if ready and attempt == 0:
                    continue
                detail = f"：{prepare_error}" if prepare_error else ""
                return f"[sandbox_unavailable] Docker 拒绝执行，容器状态为 {status}{detail}"
        return "[sandbox_unavailable] Docker 拒绝执行。"
    except Exception as e:
        return f"exec error: {e}"
    finally:
        lock.release()


def attach_shell(container_id: str, shell: str = "/bin/bash", workdir: str = "/workplace"):
    """Return (socket, exec_id, client) for interactive PTY or (None, None, None)."""
    client = get_docker_client()
    if not client or not container_id:
        return None, None, None
    shell = shell if shell.startswith("/") else f"/bin/{shell}"
    if shell == "/bin/sh":
        cmd = (
            f"cd {workdir} 2>/dev/null || mkdir -p {workdir}; cd {workdir}; "
            f"export PS1='\\u@\\h:\\w# '; exec /bin/sh -l"
        )
    else:
        cmd = (
            f"cd {workdir} 2>/dev/null || mkdir -p {workdir}; cd {workdir}; "
            f"export PS1='\\u@\\h:\\w# '; exec {shell} -l"
        )
    try:
        c = client.containers.get(container_id)
        exec_id = client.api.exec_create(
            c.id,
            ["/bin/sh", "-c", cmd],
            stdin=True,
            tty=True,
            environment={"TERM": "xterm-256color"},
            workdir=workdir,
        )["Id"]
        sock = client.api.exec_start(exec_id, socket=True, tty=True)
        return sock, exec_id, client
    except Exception as e:
        logger.error("attach_shell failed: %s", e)
        return None, None, None


def resize_shell(client, exec_id: str, cols: int, rows: int) -> None:
    if not client or not exec_id:
        return
    try:
        client.api.exec_resize(exec_id, height=rows, width=cols)
    except Exception:
        pass


def get_container_logs(container_id: str, tail: int = 100) -> str:
    client = get_docker_client()
    if not client or not container_id:
        return ""
    try:
        c = client.containers.get(container_id)
        return c.logs(tail=tail).decode("utf-8", errors="replace")
    except Exception as e:
        return str(e)


def list_containers() -> list[dict]:
    client = get_docker_client()
    if not client:
        return []
    result = []
    for c in client.containers.list(all=True):
        result.append({
            "id": c.short_id.replace("sha256:", "")[:12],
            "name": c.name,
            "status": c.status,
            "image": (c.image.tags or ["<none>"])[0],
        })
    return result


def _mirror_pull_ref(name: str) -> tuple[str, str]:
    """Return (pull_ref, local_ref). Use mirror prefix for simple docker.io names."""
    name = name.strip()
    settings = get_settings()
    mirror = (settings.docker_registry_mirror or "").strip().rstrip("/")
    if not mirror or "/" in name:
        return name, name
    return f"{mirror}/{name}", name


def pull_image(name: str) -> str:
    client = get_docker_client()
    if not client:
        return "Docker 不可用"
    try:
        pull_ref, local_ref = _mirror_pull_ref(name)
        pulled = client.images.pull(pull_ref)
        if pull_ref != local_ref:
            imgs = pulled if isinstance(pulled, list) else [pulled]
            repo = local_ref.split(":")[0]
            tag = local_ref.split(":", 1)[1] if ":" in local_ref else "latest"
            for img in imgs:
                img.tag(repo, tag)
        return "拉取成功"
    except Exception as e:
        return f"拉取失败: {e}"


def delete_image(image_id: str) -> str:
    client = get_docker_client()
    if not client:
        return "Docker 不可用"
    try:
        client.images.remove(image_id, force=True)
        return "删除成功"
    except Exception as e:
        return f"删除失败: {e}"


def sanitize_image_repo(name: str) -> str:
    raw = (name or "gap-snapshot").strip()
    parts = raw.split("/")
    cleaned: list[str] = []
    for part in parts:
        p = part.lower()
        p = re.sub(r"[^a-z0-9._-]", "-", p)
        p = re.sub(r"-+", "-", p).strip("-")
        if p:
            cleaned.append(p)
    return "/".join(cleaned) if cleaned else "gap-snapshot"


def commit_image(container_id: str, repo: str, tag: str = "latest") -> tuple[str, str]:
    """Commit running container to local image. Returns (image_ref, image_id_or_error)."""
    client = get_docker_client()
    if not client or not container_id:
        return "", "Docker 不可用"
    repo = sanitize_image_repo(repo.split(":")[0] if ":" in repo else repo)
    tag = (tag or "latest").strip() or "latest"
    try:
        c = client.containers.get(container_id)
        if c.status != "running":
            return "", "容器未运行，无法保存镜像"
        img = c.commit(repository=repo, tag=tag)
        return f"{repo}:{tag}", img.id
    except Exception as e:
        logger.error("commit_image failed: %s", e)
        return "", f"保存失败: {e}"
