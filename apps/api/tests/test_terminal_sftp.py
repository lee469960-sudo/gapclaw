"""SSH terminal SFTP upload/download endpoints."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.deps import get_session_user
from app.models import SshServer, User
from app.routers import terminal as term_router
from app.services.ssh_service import resolve_remote_path


class _FakeSftp:
    def normalize(self, path: str) -> str:
        assert path == "."
        return "/home/ubuntu"


def test_resolve_remote_path_home_and_filename():
    sftp = _FakeSftp()
    assert resolve_remote_path(sftp, ".") == "/home/ubuntu"
    assert resolve_remote_path(sftp, "~") == "/home/ubuntu"
    assert resolve_remote_path(sftp, "~/notes") == "/home/ubuntu/notes"
    assert resolve_remote_path(sftp, ".", filename="a.txt") == "/home/ubuntu/a.txt"
    assert resolve_remote_path(sftp, "/tmp", filename="../etc/passwd") == "/tmp/passwd"


def _client(db, user: User):
    app = FastAPI()
    app.include_router(term_router.router)
    app.dependency_overrides[get_session_user] = lambda: user

    def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def _db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _server(db, *, creator="alice", visibility="private"):
    s = SshServer(
        id="srv1",
        name="box",
        host="10.0.0.6",
        port=22,
        username="ubuntu",
        creator=creator,
        visibility=visibility,
        allowed_users="[]",
    )
    db.add(s)
    db.commit()
    return s


def test_sftp_download_returns_file(monkeypatch):
    db = _db()
    _server(db)
    monkeypatch.setattr(
        "app.services.ssh_service.sftp_download",
        lambda server, path: b"hello " + path.encode(),
    )
    client = _client(db, User(username="alice", password_hash="x", roles="[]", disabled=False))
    res = client.get(
        "/pages/page_terminal.cgi",
        params={"action": "sftp_download", "id": "srv1", "path": "/tmp/a.txt"},
    )
    assert res.status_code == 200
    assert res.content == b"hello /tmp/a.txt"
    assert "a.txt" in res.headers.get("content-disposition", "")


def test_sftp_download_denied_for_other_user():
    db = _db()
    _server(db, creator="alice", visibility="private")
    client = _client(db, User(username="bob", password_hash="x", roles="[]", disabled=False))
    res = client.get(
        "/pages/page_terminal.cgi",
        params={"action": "sftp_download", "id": "srv1", "path": "/tmp/a.txt"},
    )
    assert res.json()["code"] != 0


def test_sftp_upload_multipart(monkeypatch):
    db = _db()
    _server(db)
    seen = {}

    def fake_upload(server, remote_dir, data, filename=None):
        seen["dir"] = remote_dir
        seen["data"] = data
        seen["filename"] = filename
        seen["host"] = server.host
        return f"{remote_dir.rstrip('/')}/{filename}"

    monkeypatch.setattr("app.services.ssh_service.sftp_upload", fake_upload)
    client = _client(db, User(username="alice", password_hash="x", roles="[]", disabled=False))
    res = client.post(
        "/pages/page_terminal.cgi",
        data={"action": "sftp_upload", "id": "srv1", "path": "/tmp"},
        files={"file": ("notes.md", b"# hi", "text/markdown")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["code"] == 0
    assert body["data"]["path"] == "/tmp/notes.md"
    assert seen == {
        "dir": "/tmp",
        "data": b"# hi",
        "filename": "notes.md",
        "host": "10.0.0.6",
    }


def test_sftp_upload_denied_for_other_user():
    db = _db()
    _server(db, creator="alice", visibility="private")
    client = _client(db, User(username="bob", password_hash="x", roles="[]", disabled=False))
    res = client.post(
        "/pages/page_terminal.cgi",
        data={"action": "sftp_upload", "id": "srv1", "path": "/tmp"},
        files={"file": ("notes.md", b"# hi", "text/markdown")},
    )
    assert res.json()["code"] != 0
