#!/usr/bin/env bash
# Start Hermes Desktop from this checkout: Vite renderer + Electron + the
# Python backend. Same as `npm run dev`, except it first pins the backend
# interpreter to one that can actually import Hermes from here -- so a worktree
# with no venv starts instead of dying on `No module named 'yaml'`.
#
# Extra args go to `npm run dev`.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

export HERMES_DESKTOP_PYTHON=${HERMES_DESKTOP_PYTHON:-$("$here/dev-python.sh")}
echo "[dev] backend python: $HERMES_DESKTOP_PYTHON"

# Put that interpreter's bin on PATH too, so console scripts the backend shells
# out to (`hermes`, ...) exist. They import from PYTHONPATH first, which the
# desktop points at this checkout -- so they run these edits, not the install's.
# Electron passes its PATH through to the backend (buildDesktopBackendPath),
# which otherwise only prepends <root>/venv/bin -- a path that doesn't exist
# when the interpreter came from HERMES_DESKTOP_PYTHON.
export PATH="$(dirname "$HERMES_DESKTOP_PYTHON"):$PATH"

cd "$here/.."
exec npm run dev "$@"
