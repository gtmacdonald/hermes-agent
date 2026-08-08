#!/usr/bin/env bash
# Print a Python interpreter that can run the Hermes backend from THIS checkout.
#
# The desktop boots the backend with whatever interpreter it finds for the
# source root. A fresh `git worktree add` has no venv (venv/ is gitignored), so
# that search falls through to system python3 -- which usually has no PyYAML and
# dies with ModuleNotFoundError at hermes_cli/config.py import time, before the
# gateway ever binds a port.
#
# Preference order: this checkout's venv, then the installed Hermes venv (whose
# deps are the same set, and which is already built).
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
hermes_home=${HERMES_HOME:-$HOME/.hermes}

for py in \
  "$repo_root/venv/bin/python" \
  "$repo_root/.venv/bin/python" \
  "$hermes_home/hermes-agent/venv/bin/python"; do
  # Same probe the desktop runs (electron/backend-probes.ts): the imports must
  # resolve against THIS checkout, so PYTHONPATH points here, not at the venv's
  # own copy of Hermes.
  if [ -x "$py" ] && PYTHONPATH="$repo_root" "$py" -c 'import yaml, dotenv, hermes_cli.config' 2>/dev/null; then
    echo "$py"
    exit 0
  fi
done

echo "No Python can run the Hermes backend from $repo_root." >&2
echo "Install Hermes (scripts/install.sh), or build a venv for this checkout:" >&2
echo "  python3 -m venv '$repo_root/venv' && '$repo_root/venv/bin/pip' install -e '$repo_root'" >&2
exit 1
