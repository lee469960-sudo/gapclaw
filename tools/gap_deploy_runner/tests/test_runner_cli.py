from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.gap_deploy_runner.cli import build_parser, main


class Runtime:
    def __init__(self, **kwargs):
        self.kwargs, self.calls = kwargs, []

    def dispatch(self, operation, *, manifest_payload=None):
        self.calls.append((operation, manifest_payload))
        return {"operation": operation}


def test_cli_executes_only_fixed_operations_with_their_expected_inputs(tmp_path: Path, capsys):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"target_id": "staging"}), encoding="utf-8")
    runtimes = []

    def factory(**kwargs):
        runtime = Runtime(**kwargs)
        runtimes.append(runtime)
        return runtime

    assert main(["--root", str(tmp_path), "--app-env", str(tmp_path / "app.env"), "deploy", "--manifest", str(manifest)], runtime_factory=factory) == 0
    assert main(["--root", str(tmp_path), "status"], runtime_factory=factory) == 0
    assert main(["--root", str(tmp_path), "health"], runtime_factory=factory) == 0
    assert main(["--root", str(tmp_path), "rollback"], runtime_factory=factory) == 0

    assert [runtime.calls[0][0] for runtime in runtimes] == ["deploy", "status", "health", "rollback"]
    assert runtimes[0].calls[0][1] == {"target_id": "staging"}
    assert all(call[1] is None for runtime in runtimes[1:] for call in runtime.calls)
    assert '"operation": "rollback"' in capsys.readouterr().out


def test_cli_parser_rejects_arbitrary_operation_or_rollback_input():
    with pytest.raises(SystemExit):
        build_parser().parse_args(["shell", "id"])
    with pytest.raises(SystemExit):
        build_parser().parse_args(["rollback", "--command", "id"])
