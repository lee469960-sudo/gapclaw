#!/bin/sh
set -eu

source_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
runner_root=${1:-/opt/gap-runner}
library_root="$runner_root/lib/tools/gap_deploy_runner"

install -d -m 0750 -o root -g gap-runner "$library_root" "$runner_root/bin"
install -m 0640 -o root -g gap-runner "$source_root"/*.py "$library_root/"
install -m 0750 -o root -g gap-runner "$(dirname -- "$0")/gap-deploy-runner" "$runner_root/bin/gap-deploy-runner"
install -m 0750 -o root -g gap-runner "$(dirname -- "$0")/gap-deploy-runner-server" "$runner_root/bin/gap-deploy-runner-server"
