"""react-engine-v11 regression: LLM group member validation + cycle detection.

Task map (see openspec/changes/react-engine-v11/tasks.md):
  3.1 member validation (R1)
  3.2 cycle detection (R2)
  3.3 single-layer flat failure (R2)
  3.4 full-suite regression (`python -m pytest tests/ -q`)
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models import LLMResource
from app.routers.llm import LLMBody, llm_post
from app.services.llm_client import LLMGroupCycleError, chat_completion


def _make_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _leaf(db, lid):
    db.add(
        LLMResource(
            id=lid, type="llm", name=lid, provider="openai",
            base_url="https://example.test/v1", api_key_enc="enc", model="m",
        )
    )
    db.commit()
    return db.query(LLMResource).filter(LLMResource.id == lid).first()


def _group(db, gid, members):
    db.add(LLMResource(id=gid, type="group", name=gid, members=json.dumps(members)))
    db.commit()
    return db.query(LLMResource).filter(LLMResource.id == gid).first()


_USER = SimpleNamespace(username="t")


def _post(db, body):
    return asyncio.run(llm_post(body, _USER, db))


# ---------------------------------------------------------------------------
# 3.1 成员校验 (R1)
# ---------------------------------------------------------------------------

def test_member_not_exist_rejected():
    db = _make_db()
    _leaf(db, "leaf1")
    _group(db, "g1", ["leaf1"])
    body = LLMBody(action="update", id="g1", type="group", name="g1", members=["nope"])
    resp = _post(db, body)
    assert resp["code"] == 1
    assert "不存在" in resp["msg"]
    # 不保存：members 保持原值
    row = db.query(LLMResource).filter(LLMResource.id == "g1").first()
    assert json.loads(row.members) == ["leaf1"]


def test_member_self_reference_rejected():
    db = _make_db()
    _group(db, "g1", [])
    body = LLMBody(action="update", id="g1", type="group", name="g1", members=["g1"])
    resp = _post(db, body)
    assert resp["code"] == 1
    assert "自引用" in resp["msg"]
    row = db.query(LLMResource).filter(LLMResource.id == "g1").first()
    assert json.loads(row.members) == []


def test_member_group_nested_rejected():
    db = _make_db()
    _group(db, "g2", [])  # g2 是 group（叶子校验应拒绝组套组）
    body = LLMBody(action="create", type="group", name="g3", members=["g2"])
    resp = _post(db, body)
    assert resp["code"] == 1
    assert "组套组" in resp["msg"]
    db.rollback()  # 未 commit 的 item 被丢弃
    assert db.query(LLMResource).filter(LLMResource.name == "g3").first() is None


def test_leaf_members_saved():
    db = _make_db()
    _leaf(db, "leaf1")
    _leaf(db, "leaf2")
    body = LLMBody(action="create", type="group", name="g1", members=["leaf1", "leaf2"])
    resp = _post(db, body)
    assert resp["code"] == 0
    row = db.query(LLMResource).filter(LLMResource.name == "g1").first()
    assert row is not None
    assert json.loads(row.members) == ["leaf1", "leaf2"]


def test_llm_members_cleared():
    db = _make_db()
    _leaf(db, "leaf1")
    _leaf(db, "leaf2")
    body = LLMBody(action="update", id="leaf1", type="llm", name="leaf1", members=["leaf2"])
    resp = _post(db, body)
    assert resp["code"] == 0
    row = db.query(LLMResource).filter(LLMResource.id == "leaf1").first()
    assert json.loads(row.members) == []


# ---------------------------------------------------------------------------
# 3.2 环检测 (R2)
# ---------------------------------------------------------------------------

def test_group_self_reference_raises_cycle_error():
    db = _make_db()
    _group(db, "g1", ["g1"])
    llm = db.query(LLMResource).filter(LLMResource.id == "g1").first()
    with pytest.raises(LLMGroupCycleError):
        asyncio.run(chat_completion(llm, [{"role": "user", "content": "hi"}], db=db))


def test_group_cycle_a_b_a_raises_cycle_error():
    db = _make_db()
    _group(db, "ga", ["gb"])
    _group(db, "gb", ["ga"])
    llm = db.query(LLMResource).filter(LLMResource.id == "ga").first()
    with pytest.raises(LLMGroupCycleError):
        asyncio.run(chat_completion(llm, [{"role": "user", "content": "hi"}], db=db))


def test_group_depth_limit_raises_cycle_error():
    db = _make_db()
    _leaf(db, "leaf1")
    ids = [f"g{i}" for i in range(9)]  # g0..g8，深度超 8 上限
    for i, gid in enumerate(ids):
        nxt = ids[i + 1] if i + 1 < len(ids) else "leaf1"
        _group(db, gid, [nxt])
    llm = db.query(LLMResource).filter(LLMResource.id == "g0").first()
    with pytest.raises(LLMGroupCycleError):
        asyncio.run(chat_completion(llm, [{"role": "user", "content": "hi"}], db=db))


# ---------------------------------------------------------------------------
# 3.3 单层失败 (R2)
# ---------------------------------------------------------------------------

class _StatusResponse:
    def __init__(self, status):
        self.status = status

    def raise_for_status(self):
        req = httpx.Request("POST", "https://example.test/v1/chat/completions")
        resp = httpx.Response(self.status, request=req)
        raise httpx.HTTPStatusError(f"{self.status} error", request=req, response=resp)


class _StatusClient:
    def __init__(self, status=529):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, *_args, **_kwargs):
        return _StatusResponse(self.status)


def test_group_flat_failures_single_layer_message():
    db = _make_db()
    _leaf(db, "leaf1")
    _leaf(db, "leaf2")
    _group(db, "g1", ["leaf1", "leaf2"])
    llm = db.query(LLMResource).filter(LLMResource.id == "g1").first()

    async def _run():
        with patch("app.services.llm_client.decrypt_secret", return_value="secret"):
            with patch(
                "app.services.llm_client.httpx.AsyncClient",
                lambda **kw: _StatusClient(status=529),
            ):
                with patch("app.services.llm_client.asyncio.sleep", new=AsyncMock()):
                    try:
                        await chat_completion(
                            llm, [{"role": "user", "content": "hi"}], db=db
                        )
                        raise AssertionError("should raise RuntimeError")
                    except RuntimeError as e:
                        return str(e)

    msg = asyncio.run(_run())
    assert msg.count("模型组全部失败") == 1  # 单层，不嵌套爆炸
    assert "529" in msg
