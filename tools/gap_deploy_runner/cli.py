"""Local CLI parser for the fixed Deploy Runner operation set."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Callable, Sequence

from tools.gap_deploy_runner.runtime import DeployRunnerRuntime, RunnerRuntimeError
from tools.gap_deploy_runner.runner import RunnerOperation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gap-deploy-runner")
    parser.add_argument("--root", type=Path, default=Path("/opt/gap-runner"))
    parser.add_argument("--app-env", type=Path, default=Path("/opt/gap/.env"))
    subcommands = parser.add_subparsers(dest="operation", required=True)
    deploy = subcommands.add_parser(RunnerOperation.DEPLOY.value)
    deploy.add_argument("--manifest", type=Path, required=True)
    subcommands.add_parser(RunnerOperation.STATUS.value)
    subcommands.add_parser(RunnerOperation.HEALTH.value)
    subcommands.add_parser(RunnerOperation.ROLLBACK.value)
    return parser


def main(argv: Sequence[str] | None = None, *, runtime_factory: Callable[..., DeployRunnerRuntime] = DeployRunnerRuntime) -> int:
    args = build_parser().parse_args(argv)
    try:
        runtime = runtime_factory(root=args.root, app_env_file=args.app_env)
        payload = json.loads(args.manifest.read_text(encoding="utf-8")) if args.operation == "deploy" else None
        result = runtime.dispatch(args.operation, manifest_payload=payload)
    except (OSError, ValueError, RunnerRuntimeError) as exc:
        print(json.dumps({"reason": getattr(exc, "reason", "runner_manifest_invalid")}, sort_keys=True))
        return 2
    except Exception as exc:
        print(json.dumps({"reason": getattr(exc, "reason", "runner_operation_failed")}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0
