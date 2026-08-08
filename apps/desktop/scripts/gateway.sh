#!/usr/bin/env bash
# Start ONLY the Hermes backend/gateway from this checkout -- no Electron, no
# Vite. This is the exact command the desktop spawns, so when the gateway is
# what's broken you see its startup errors directly instead of through
# ~/.hermes/logs/desktop.log and a repair loop.
#
# Defaults to `serve --host 127.0.0.1 --port 0` (ephemeral port, prints
# HERMES_BACKEND_READY). Any args replace that, e.g. `gateway.sh doctor`.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd "$here/../../.." && pwd)
py=${HERMES_DESKTOP_PYTHON:-$("$here/dev-python.sh")}

if [ $# -eq 0 ]; then
  set -- serve --host 127.0.0.1 --port 0
fi

cd "$repo_root"
exec env \
  PYTHONPATH="$repo_root" \
  PYTHONUTF8=1 \
  PATH="$(dirname "$py"):$PATH" \
  "$py" -m hermes_cli.main "$@"
