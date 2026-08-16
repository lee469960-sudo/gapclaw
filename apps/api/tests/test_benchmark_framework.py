from __future__ import annotations

import json
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmark.adapters.workbuddy import WorkBuddyAdapter
from benchmark.compare.version_compare import compare_runs
from benchmark.metrics.aggregator import evaluate_gate, summarize_results
from benchmark.metrics.failure_reason import FailureReason
from benchmark.metrics.metrics import TaskMetrics, TaskResult
from benchmark.runner.benchmark_runner import BenchmarkRunner, load_failed_task_ids
from benchmark.trace.analyzer import LoopDetectionConfig, LoopDetectionState, action_fingerprint
from benchmark.trace.models import RunTrace, StepTrace
from benchmark.trace.recorder import TraceRecorder
from benchmark.scripts.check_react_benchmark_env import _check_minimax_url
from benchmark.scripts.run_react_engine_agent import _make_session_id, _render_user_message


def _result(task_id: str, score: float, category: str = "code") -> TaskResult:
    return TaskResult(
        run_id="run",
        benchmark="workbuddy",
        task_id=task_id,
        category=category,
        engine_version="react-test",
        git_commit="unknown",
        branch="unknown",
        model="test-model",
        model_provider="test",
        benchmark_version="fixture",
        started_at="2026-08-11T00:00:00Z",
        finished_at="2026-08-11T00:00:01Z",
        status="SUCCESS" if score >= 1.0 else "FAILED",
        score=score,
        metrics=TaskMetrics(steps=2, tool_calls=1, tool_failures=0),
        failure_reason=FailureReason.SUCCESS if score >= 1.0 else FailureReason.SCORER_FAILED,
    )


def test_workbuddy_adapter_loads_jsonl_and_isolates_workspace(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "tasks.jsonl").write_text(
        json.dumps({
            "task_id": "WB-CODE-001",
            "category": "code",
            "instruction": "write output",
            "metadata": {"expected_file": "out.txt"},
        }) + "\n",
        encoding="utf-8",
    )
    adapter = WorkBuddyAdapter(dataset)
    task = adapter.load_tasks()[0]
    workspace = adapter.prepare_workspace(task, tmp_path / "run-workspaces")
    assert workspace != dataset
    assert (workspace / "instruction.txt").read_text(encoding="utf-8") == "write output"
    (workspace / "out.txt").write_text("ok", encoding="utf-8")
    assert adapter.evaluate(task, workspace).passed


def test_workbuddy_adapter_loads_official_native_layout(tmp_path):
    dataset = tmp_path / "dataset"
    task_a = dataset / "extracted" / "wb-bench-sec-v1.0" / "tasks" / "readme-task"
    task_b = dataset / "extracted" / "wb-bench-sec-v1.0" / "tasks" / "steps-task"
    task_a.mkdir(parents=True)
    task_b.mkdir(parents=True)
    (dataset / "extracted" / "wb-bench-sec-v1.0" / "dataset.toml").write_text(
        '[dataset]\nid = "wb-bench-sec-v1.0"\ntask_count = 2\n',
        encoding="utf-8",
    )
    task_toml = (
        'schema_version = "1.3"\n'
        '[task]\nname = "codebuddy/example"\n'
        '[metadata]\nsource_case = "example"\ncategory = "security-operation"\n'
        '[verifier]\ntimeout_sec = 60.0\n'
        '[agent]\ntimeout_sec = 60.0\n'
        '[environment]\nnetwork_mode = "public"\n'
    )
    (task_a / "task.toml").write_text(task_toml, encoding="utf-8")
    (task_a / "README.md").write_text("Read README instruction", encoding="utf-8")
    env_a = task_a / "environment"
    env_a.mkdir()
    (env_a / "sample.txt").write_text("asset", encoding="utf-8")

    (task_b / "task.toml").write_text(
        task_toml.replace('source_case = "example"', 'source_case = "steps-example"'),
        encoding="utf-8",
    )
    step_dir = task_b / "steps" / "one"
    step_dir.mkdir(parents=True)
    (step_dir / "instruction.md").write_text("Step instruction", encoding="utf-8")
    env_b = task_b / "environment"
    env_b.mkdir()
    src_file = tmp_path / "archive-file.txt"
    src_file.write_text("archived", encoding="utf-8")
    with tarfile.open(env_b / "workspace.tar.gz", "w:gz") as tf:
        tf.add(src_file, arcname="archive-file.txt")

    adapter = WorkBuddyAdapter(dataset)
    tasks = adapter.load_tasks()
    assert len(tasks) == 2
    assert {t.metadata["instruction_source"] for t in tasks} == {"README.md", "steps/*/instruction.md"}

    with_workspace = next(t for t in tasks if t.task_id == "WB-SECURITY-steps-example")
    workspace = adapter.prepare_workspace(with_workspace, tmp_path / "run")
    assert (workspace / "archive-file.txt").read_text(encoding="utf-8") == "archived"


def test_trace_recorder_redacts_sensitive_values(tmp_path):
    trace = RunTrace(
        run_id="r1",
        benchmark="workbuddy",
        task_id="t1",
        category="code",
        engine_version="v",
        git_commit="g",
        model="m",
        started_at="now",
    )
    path = tmp_path / "trace.json"
    recorder = TraceRecorder(trace, path)
    recorder.record_step(StepTrace(
        step=1,
        action={"tool": "http", "arguments": {"Authorization": "Bearer abc123", "api_key": "secret"}},
    ))
    recorder.finish(status="FAILED", score=0.0, finished_at="later")
    recorder.write()
    data = json.loads(path.read_text(encoding="utf-8"))
    args = data["steps"][0]["action"]["arguments"]
    assert args["Authorization"] == "[REDACTED]"
    assert args["api_key"] == "[REDACTED]"


def test_metrics_aggregation_and_gate():
    summary = summarize_results([
        _result("a", 1.0, "code"),
        _result("b", 0.0, "web"),
    ])
    assert summary["success_rate"] == 0.5
    assert summary["category_success_rate"]["code"] == 1.0
    assert summary["failure_distribution"]["SCORER_FAILED"] == 1
    gate = evaluate_gate(summary, {"total_success": {"min": 0.6}})
    assert not gate.passed


def test_loop_fingerprint_detects_repetition():
    fp = action_fingerprint("query_ads_view", {"sql": "select * from t", "limit": 10}, "fetch rows")
    same = action_fingerprint("query_ads_view", {"limit": 10, "sql": "select  *  from t"}, "fetch rows")
    assert fp == same
    state = LoopDetectionState(LoopDetectionConfig(recent_window=4, repeat_threshold=3))
    assert state.observe(fp) == "OK"
    assert state.observe(fp) == "OK"
    assert state.observe(fp) == "LOOP_WARNING"
    assert state.observe(fp) == "LOOP_DETECTED"


def test_benchmark_runner_smoke_writes_outputs(tmp_path):
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    (dataset / "tasks.jsonl").write_text(
        "\n".join([
            json.dumps({
                "task_id": "WB-CODE-001",
                "category": "code",
                "instruction": "expected_file: out.txt",
                "metadata": {"expected_file": "out.txt"},
            }),
            json.dumps({
                "task_id": "WB-WEB-001",
                "category": "web",
                "instruction": "expected_file: web.txt",
                "metadata": {"expected_file": "web.txt"},
            }),
        ]) + "\n",
        encoding="utf-8",
    )
    config = {
        "benchmark": "workbuddy",
        "dataset_path": str(dataset),
        "results_dir": str(tmp_path / "results"),
        "engine_version": "react-test",
        "benchmark_version": "fixture",
        "limits": {"task_timeout_seconds": 30},
        "agent": {
            "command": [
                sys.executable,
                "-m",
                "benchmark.scripts.fixture_agent",
                "--instruction-file",
                "{instruction_file}",
                "--workspace",
                "{workspace}",
            ],
        },
        "gate": {"total_success": {"min": 1.0}},
    }
    result = BenchmarkRunner(config=config, mode="smoke").run()
    assert result.gate_passed is True
    assert (result.output_dir / "summary.json").exists()
    assert (result.output_dir / "summary.md").exists()
    assert (result.output_dir / "failure-analysis.md").exists()
    assert (result.output_dir / "tasks.jsonl").exists()
    rows = [
        json.loads(line)
        for line in (result.output_dir / "tasks.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert len(rows) == 2
    assert all(row["failure_reason"] == "SUCCESS" for row in rows)


def test_version_compare_tracks_improved_and_regressed(tmp_path):
    run_a = tmp_path / "a"
    run_b = tmp_path / "b"
    run_a.mkdir()
    run_b.mkdir()
    (run_a / "summary.json").write_text(json.dumps({"success_rate": 0.5}), encoding="utf-8")
    (run_b / "summary.json").write_text(json.dumps({"success_rate": 0.5}), encoding="utf-8")
    (run_a / "tasks.jsonl").write_text(
        json.dumps(_result("improved", 0.0).to_dict()) + "\n"
        + json.dumps(_result("regressed", 1.0).to_dict()) + "\n"
        + json.dumps(_result("same", 1.0).to_dict()) + "\n",
        encoding="utf-8",
    )
    (run_b / "tasks.jsonl").write_text(
        json.dumps(_result("improved", 1.0).to_dict()) + "\n"
        + json.dumps(_result("regressed", 0.0).to_dict()) + "\n"
        + json.dumps(_result("same", 1.0).to_dict()) + "\n",
        encoding="utf-8",
    )
    comparison = compare_runs(run_a, run_b)
    assert comparison.improved_tasks == ["improved"]
    assert comparison.regressed_tasks == ["regressed"]
    assert comparison.unchanged_tasks == ["same"]
    assert not comparison.gate_passed


def test_load_failed_task_ids_for_rerun(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "tasks.jsonl").write_text(
        json.dumps(_result("ok", 1.0).to_dict()) + "\n"
        + json.dumps(_result("bad", 0.0).to_dict()) + "\n",
        encoding="utf-8",
    )
    assert load_failed_task_ids(run, failed_only=True) == ["bad"]
    assert load_failed_task_ids(run, failed_only=False) == ["ok", "bad"]


def test_react_bridge_renders_workspace_mapping(tmp_path):
    instruction_file = tmp_path / "instruction.txt"
    instruction_file.write_text("Create /workspace/www/index.html", encoding="utf-8")
    workspace = tmp_path / "workspace"
    message = _render_user_message(instruction_file, workspace, "WB-WEB-T25")
    assert "Treat that directory as the `/workspace` root" in message
    assert str(workspace) in message
    assert "WB-WEB-T25" in message
    assert "Create /workspace/www/index.html" in message


def test_react_bridge_session_id_is_bounded():
    session_id = _make_session_id("bench", "WB-WEB-" + ("x" * 200))
    assert session_id.startswith("bench_")
    assert len(session_id) <= 64


def test_minimax_url_check_flags_known_bad_host():
    bad = _check_minimax_url("https://api.minimaxi.com/v1")
    assert bad.ok is False
    assert "expected https://api.minimax.io/v1" in bad.detail

    good = _check_minimax_url("https://api.minimax.io/v1")
    assert good.ok is True
