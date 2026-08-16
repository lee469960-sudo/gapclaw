import io
import stat
import tempfile
from pathlib import Path

from app.security import decrypt_secret


def _load_private_key(key_str: str, passphrase: str | None = None):
    import paramiko
    buf = io.StringIO(key_str)
    pwd = passphrase or None
    for key_cls in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey, paramiko.DSSKey):
        buf.seek(0)
        try:
            return key_cls.from_private_key(buf, password=pwd)
        except Exception:
            continue
    raise ValueError("无法解析私钥，请检查格式或密码短语")


def _connect(server):
    import paramiko
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs = {"hostname": server.host, "port": server.port, "username": server.username, "timeout": 15}
    if server.auth_type == "key" and server.private_key_enc:
        key_str = decrypt_secret(server.private_key_enc)
        pkey = _load_private_key(key_str, decrypt_secret(server.passphrase_enc) or None)
        kwargs["pkey"] = pkey
    else:
        kwargs["password"] = decrypt_secret(server.password_enc)
    client.connect(**kwargs)
    return client


def test_ssh(server) -> dict:
    import time
    try:
        t0 = time.time()
        client = _connect(server)
        _, stdout, _ = client.exec_command("echo ok", timeout=10)
        out = stdout.read().decode().strip()
        latency = int((time.time() - t0) * 1000)
        client.close()
        return {"connected": True, "banner": out, "latency_ms": latency}
    except Exception as e:
        return {"connected": False, "error": str(e)}


def sftp_list(server, cwd: str) -> list[dict]:
    try:
        client = _connect(server)
        sftp = client.open_sftp()
        files = []
        for attr in sftp.listdir_attr(cwd):
            files.append({"name": attr.filename, "is_dir": stat.S_ISDIR(attr.st_mode), "size": attr.st_size})
        sftp.close()
        client.close()
        return files
    except Exception as e:
        return [{"name": f"error: {e}", "is_dir": False, "size": 0}]


def sftp_download(server, remote_path: str) -> bytes:
    client = _connect(server)
    sftp = client.open_sftp()
    buf = io.BytesIO()
    sftp.getfo(remote_path, buf)
    sftp.close()
    client.close()
    return buf.getvalue()


def sftp_upload(server, remote_path: str, data: bytes) -> str:
    client = _connect(server)
    sftp = client.open_sftp()
    buf = io.BytesIO(data)
    sftp.putfo(buf, remote_path)
    sftp.close()
    client.close()
    return "ok"


def sftp_mkdir(server, remote_path: str) -> str:
    client = _connect(server)
    sftp = client.open_sftp()
    sftp.mkdir(remote_path)
    sftp.close()
    client.close()
    return "ok"


def sftp_delete(server, remote_path: str) -> str:
    client = _connect(server)
    sftp = client.open_sftp()
    try:
        sftp.remove(remote_path)
    except IOError:
        import shutil
        for item in sftp.listdir_attr(remote_path):
            sftp.remove(f"{remote_path.rstrip('/')}/{item.filename}")
        sftp.rmdir(remote_path)
    sftp.close()
    client.close()
    return "ok"
