"""Restricted mTLS server entrypoint for the installed Deploy Runner."""

from __future__ import annotations

import argparse
from pathlib import Path

from tools.gap_deploy_runner.mtls import MtlsFiles, RunnerHttpApi, create_server
from tools.gap_deploy_runner.runtime import DeployRunnerRuntime, RunnerRuntimeError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gap-deploy-runner-server")
    parser.add_argument("--root", type=Path, default=Path("/opt/gap-runner"))
    parser.add_argument("--app-env", type=Path, default=Path("/opt/gap/.env"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        runtime = DeployRunnerRuntime(root=args.root, app_env_file=args.app_env)
        host, separator, port = runtime.listen.rpartition(":")
        if not separator or not host or not port.isdigit():
            raise RunnerRuntimeError("runner_listen_invalid")
        server = create_server(
            (host, int(port)),
            RunnerHttpApi(runtime, state_store=runtime.store),
            MtlsFiles(*runtime.installation.tls_files),
        )
    except RunnerRuntimeError as exc:
        print(exc.reason)
        return 2
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
