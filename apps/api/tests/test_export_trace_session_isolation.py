import json
import os
import time

from app.services.export_trace import load_latest_export_trace


def _run(root, run_id: str, session_id: str, marker: str) -> None:
    directory = root / "task" / run_id
    directory.mkdir(parents=True)
    (directory / "export_trace.json").write_text(
        json.dumps({"run_id": run_id, "source_brief": marker}),
        encoding="utf-8",
    )
    (directory / "_run_state.json").write_text(
        json.dumps({"session_id": session_id}),
        encoding="utf-8",
    )


def test_latest_export_trace_respects_session_identity(tmp_path, monkeypatch):
    from app.services import export_trace

    root = tmp_path / "workplace"
    _run(root, "100", "session-a", "same")
    _run(root, "200", "session-b", "other")
    now = time.time()
    os.utime(root / "task" / "100" / "export_trace.json", (now - 10, now - 10))
    os.utime(root / "task" / "200" / "export_trace.json", (now, now))
    monkeypatch.setattr(export_trace, "ensure_workplace", lambda _sid: root)

    run_id, trace = load_latest_export_trace("sbx", session_id="session-a")
    assert run_id == "100"
    assert trace["source_brief"] == "same"
    assert load_latest_export_trace("sbx", session_id="missing") is None

    global_run, _ = load_latest_export_trace("sbx")
    assert global_run == "200"
