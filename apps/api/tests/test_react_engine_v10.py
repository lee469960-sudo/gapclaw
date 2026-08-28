"""react-engine-v10 regression: plan preflight, READ/SHELL/SEARCH dedup,
deliverable evidence in final reflection, and transport/HTTP retry backoff.

Task map (see openspec/changes/react-engine-v10/tasks.md):
  6.1 plan preflight (R1)
  6.2 dedup hit / invalidation (R2/R3)
  6.3 Verifier deliverable evidence (R4)
  6.4 retryable HTTP status backoff (R5)
  6.5 full-suite regression (run `python -m pytest tests/ -q`)
"""

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.services.agent_tools import dedup_descriptor, read_cache_key
from app.services.agent_runtime.runtime import (
    AgentRuntime,
    _apply_plan,
    _dedup_entry,
    _dedup_entry_valid,
    _dedup_hit_pointer,
    _deliverable_evidence,
    _plan_preflight_hints,
)
from app.services.llm_client import (
    LLMHTTPError,
    _retryable_status_attempts,
    chat_completion,
    format_llm_http_error,
)


# ---------------------------------------------------------------------------
# 6.1 规划前置验证 (R1): 越权/空/不可解析 → 软 coach_hint，零 LLM 调用
# ---------------------------------------------------------------------------

def test_preflight_unauthorized_action_hint():
    ctx = SimpleNamespace(allowed_actions=["shell", "file_read"])
    hints = _plan_preflight_hints(ctx, "1. 查数据\n2. MCP: describe view", [])
    assert any("无 `mcp_tool_call` 权限" in h for h in hints)


def test_preflight_authorized_plan_no_permission_hint():
    ctx = SimpleNamespace(allowed_actions=["file_read", "shell", "file_search"])
    hints = _plan_preflight_hints(
        ctx, "1. READ: 目录\n2. SHELL: 统计\n3. SEARCH: 关键词", [{}]
    )
    assert not any("权限" in h for h in hints)


def test_preflight_empty_plan_hint():
    ctx = SimpleNamespace(allowed_actions=["shell"])
    hints = _plan_preflight_hints(ctx, "   ", [])
    assert any("PLAN 为空" in h for h in hints)


def test_preflight_multiline_unparsed_hint():
    ctx = SimpleNamespace(allowed_actions=["shell"])
    hints = _plan_preflight_hints(ctx, "先看看目录\n再统计数字", [])
    assert any("未解析出子任务" in h for h in hints)


class _FakeCM:
    def __init__(self):
        self.hints = []

    def add_coach_hint(self, hint):
        self.hints.append(hint)

    def set_task_context(self, goal, plan="", view_catalog=""):
        pass


def test_apply_plan_preflight_is_local_no_llm():
    # _apply_plan is synchronous → it physically cannot await chat_completion,
    # so preflight hints are guaranteed zero-LLM (a soft local check only).
    assert not inspect.iscoroutinefunction(_apply_plan)

    cm = _FakeCM()
    ctx = SimpleNamespace(allowed_actions=["shell"], user_message="巡检")
    state = SimpleNamespace(goal="", plan_text="", subtasks=[])
    _apply_plan(ctx, state, cm, "MCP: describe x")
    assert any("无 `mcp_tool_call` 权限" in h for h in cm.hints)


# ---------------------------------------------------------------------------
# 6.2 工具结果去重复用 + 失效 (R2/R3)
# ---------------------------------------------------------------------------

def test_dedup_descriptor_read_key():
    d = dedup_descriptor("file_read", "READ: task/t1/a.txt")
    assert d["key"] == "read\x00task/t1/a.txt"
    assert d["rel"] == "task/t1/a.txt"


def test_dedup_descriptor_shell_normalizes_whitespace():
    k1 = dedup_descriptor("shell", "SHELL: ls   -la")["key"]
    k2 = dedup_descriptor("shell", "SHELL: ls -la")["key"]
    assert k1 == k2 == "shell\x00ls -la"


def test_dedup_descriptor_search_key():
    d = dedup_descriptor("file_search", "SEARCH: 逾期 金额")
    assert d["key"] == "search\x00逾期 金额"


def test_dedup_descriptor_non_dedup_actions_are_none():
    assert dedup_descriptor("file_write", "WRITE: x\ny") is None
    assert dedup_descriptor("recall", "RECALL: x") is None


def test_dedup_descriptor_read_mtime_fingerprint(tmp_path):
    (tmp_path / "task" / "t1").mkdir(parents=True)
    (tmp_path / "task" / "t1" / "a.txt").write_text("hi", encoding="utf-8")
    sandbox = SimpleNamespace(id="s1")
    with patch("app.services.agent_tools.workplace_root", return_value=tmp_path):
        hit = dedup_descriptor("file_read", "READ: task/t1/a.txt", sandbox=sandbox)
        miss = dedup_descriptor("file_read", "READ: nope.txt", sandbox=sandbox)
    assert hit["mtime"] is not None
    assert miss["mtime"] is None


def test_dedup_entry_valid_detects_mtime_change():
    desc_same = {"mtime": 1.5}
    desc_changed = {"mtime": 2.5}
    desc_nofp = {"mtime": None}
    entry = {"mtime": 1.5}
    assert _dedup_entry_valid(desc_same, entry) is True
    assert _dedup_entry_valid(desc_changed, entry) is False
    assert _dedup_entry_valid(desc_nofp, entry) is True


def test_dedup_hit_pointer_text():
    out = _dedup_hit_pointer("shell", _dedup_entry(
        {"rel": None, "mtime": None}, "shell", "abcde"))
    assert out.startswith("该结果已缓存")
    assert "SHELL" in out and "5 字符" in out


def test_write_invalidation_key_matches_read_dedup_key():
    # WRITE 后通过 read_cache_key(path) 使同路径 READ 缓存失效（R3）：
    # 失效 key 必须与 READ 去重 key 完全一致，pop 才会命中。
    rel = "task/t1/report.csv"
    assert read_cache_key(rel) == dedup_descriptor("file_read", f"READ: {rel}")["key"]


# ---------------------------------------------------------------------------
# 6.3 完成度复核交付物证据 (R4)
# ---------------------------------------------------------------------------

def test_deliverable_evidence_reads_file_head(tmp_path):
    (tmp_path / "out.csv").write_text("a,b\n1,2\n3,4\n", encoding="utf-8")
    with patch("app.services.workplace.workplace_root", return_value=tmp_path):
        ev = _deliverable_evidence(["out.csv"], SimpleNamespace(id="s1"))
    assert "out.csv" in ev
    assert "a,b" in ev and "3,4" in ev


def test_deliverable_evidence_empty():
    assert _deliverable_evidence([], None) == "（无）"
    assert _deliverable_evidence(None, None) == "（无）"


def test_reflect_final_prompt_includes_deliverable_evidence(tmp_path):
    deliverable = tmp_path / "report.csv"
    deliverable.write_text("col_a,col_b\n1,2\n3,4\n", encoding="utf-8")
    captured = {}

    async def _fake_chat(llm, messages, **kwargs):
        captured["prompt"] = messages[0]["content"]
        return "PASS"

    ctx = SimpleNamespace(
        user_message="导出报表",
        agent=SimpleNamespace(memory="", llm_timeout=120, id="a1"),
        llm="fake-llm",
        db=None,
        chat_key="a1:s1",
        sandbox=SimpleNamespace(id="s1"),
    )
    state = SimpleNamespace(
        goal="", saved_paths=["report.csv"], subtasks=[], progress_lines=[]
    )

    async def _go():
        with patch("app.services.workplace.workplace_root", return_value=tmp_path):
            with patch("app.services.llm_client.chat_completion", new=_fake_chat):
                return await AgentRuntime._reflect_final(
                    None, ctx, state, "FINAL: 完成"
                )

    result = asyncio.run(_go())
    assert result is None  # PASS → 不阻塞
    prompt = captured["prompt"]
    assert "交付物内容摘要" in prompt
    assert "report.csv" in prompt
    assert "col_a,col_b" in prompt


# ---------------------------------------------------------------------------
# 6.4 可重试 HTTP 状态码退避重试 (R5)
# ---------------------------------------------------------------------------

def test_retryable_status_attempts():
    assert _retryable_status_attempts(529) == 3
    assert _retryable_status_attempts(429) == 3
    assert _retryable_status_attempts(502) == 5
    assert _retryable_status_attempts(503) == 5
    assert _retryable_status_attempts(504) == 5
    # deterministic → 0 (no retry)
    assert _retryable_status_attempts(400) == 0
    assert _retryable_status_attempts(401) == 0
    assert _retryable_status_attempts(500) == 0
    assert _retryable_status_attempts(422) == 0


def _http_error(status: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "https://example.test/v1/chat/completions")
    resp = httpx.Response(status, request=req)
    return httpx.HTTPStatusError(f"{status} error", request=req, response=resp)


def test_format_llm_http_error_transient_messages():
    for status in (529, 429, 502, 503, 504):
        msg = format_llm_http_error(_http_error(status))
        assert str(status) in msg
        assert "退避重试" in msg


class _StatusResponse:
    def __init__(self, status):
        self.status = status

    def raise_for_status(self):
        raise _http_error(self.status)


class _StatusClient:
    calls = 0

    def __init__(self, status=529):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, *_args, **_kwargs):
        type(self).calls += 1
        return _StatusResponse(self.status)


def _llm():
    return SimpleNamespace(
        type="llm",
        api_key_enc="encrypted",
        base_url="https://example.test/v1",
        provider="openai",
        model="test-model",
        max_output_tokens=128,
        max_context_tokens=4096,
    )


def test_chat_completion_retries_retryable_http_status():
    for status, expected in ((529, 3), (429, 3), (502, 5), (503, 5), (504, 5)):
        _StatusClient.calls = 0
        llm = _llm()

        async def _run():
            with patch("app.services.llm_client.decrypt_secret", return_value="secret"):
                with patch(
                    "app.services.llm_client.httpx.AsyncClient",
                    lambda **kw: _StatusClient(status=status),
                ):
                    with patch(
                        "app.services.llm_client.asyncio.sleep", new=AsyncMock()
                    ):
                        try:
                            await chat_completion(
                                llm, [{"role": "user", "content": "hi"}]
                            )
                            raise AssertionError("should raise LLMHTTPError")
                        except LLMHTTPError as e:
                            return str(e)

        msg = asyncio.run(_run())
        assert _StatusClient.calls == expected, (status, _StatusClient.calls)
        assert str(status) in msg


def test_chat_completion_no_retry_deterministic_status():
    _StatusClient.calls = 0
    llm = _llm()

    async def _run():
        with patch("app.services.llm_client.decrypt_secret", return_value="secret"):
            with patch(
                "app.services.llm_client.httpx.AsyncClient",
                lambda **kw: _StatusClient(status=400),
            ):
                with patch("app.services.llm_client.asyncio.sleep", new=AsyncMock()):
                    try:
                        await chat_completion(
                            llm, [{"role": "user", "content": "hi"}]
                        )
                        raise AssertionError("should raise LLMHTTPError")
                    except LLMHTTPError as e:
                        return str(e)

    msg = asyncio.run(_run())
    assert _StatusClient.calls == 1
    assert "400" in msg
