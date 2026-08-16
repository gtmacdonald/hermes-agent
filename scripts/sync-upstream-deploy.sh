#!/usr/bin/env bash
# sync-upstream-deploy.sh — pull upstream main into the fork, then fast-forward
# the live install onto the fork's main. Replaces raw file copies between
# trees, which is how the 2026-08-12 stale-file breakage happened: copying
# fork files (old upstream base) over a newer install regressed core code.
#
#   fork    ~/src/hermes            upstream=NousResearch  origin=gtmacdonald
#   install ~/.hermes/hermes-agent  remote "fork" -> the fork checkout
#
# Usage: scripts/sync-upstream-deploy.sh [--push-local-commits]
# ponytail: merge-based (no rebase, no force-push); conflicts stop the script
# for hand resolution in the fork, then rerun.

set -euo pipefail

PUSH_LOCAL=false
case "${1:-}" in
  --push-local-commits) PUSH_LOCAL=true ;;
  "") ;;
  *) echo "usage: $0 [--push-local-commits]"; exit 1 ;;
esac

FORK="$HOME/src/hermes"
INSTALL="$HOME/.hermes/hermes-agent"
UV="$HOME/.hermes/bin/uv"

# ── 1. fork: bring upstream in ──────────────────────────────────────────────
cd "$FORK"
git diff --quiet && git diff --cached --quiet \
  || { echo "✗ fork has uncommitted changes — commit them first"; exit 1; }
git fetch upstream
git fetch origin
# count before merging: afterwards origin/main..HEAD includes upstream commits
LOCAL_ONLY=$(git rev-list --count origin/main..HEAD)
git merge --no-edit upstream/main \
  || { echo "✗ merge conflict — resolve in $FORK, commit, rerun"; exit 1; }
if [[ "$LOCAL_ONLY" -gt 0 && "$PUSH_LOCAL" == false ]]; then
  # the merge sits atop the local commits, so pushing anything would push them
  echo "→ $LOCAL_ONLY local commit(s) not on origin — skipping push (rerun with --push-local-commits to publish)"
else
  git push origin main
fi

# ── 2. install: fast-forward onto the fork ──────────────────────────────────
cd "$INSTALL"
git remote get-url fork >/dev/null 2>&1 || git remote add fork "$FORK"
git diff --quiet && git diff --cached --quiet \
  || { echo "✗ install has local changes — they belong in the fork; stash or move them"; exit 1; }
OLD_HEAD=$(git rev-parse HEAD)
git fetch fork
git merge --ff-only fork/main \
  || { echo "✗ install cannot fast-forward — it has commits the fork lacks; reconcile by hand"; exit 1; }

# ── 3. deps: sync venv only when the lockfile moved ─────────────────────────
if ! git diff --quiet "$OLD_HEAD"..HEAD -- uv.lock pyproject.toml; then
  echo "→ lockfile changed; syncing venv"
  # Mirror scripts/install.sh: editable [all] baseline. NOT `uv sync` — sync
  # ignores VIRTUAL_ENV (2026-08-16: silently targeted .venv for months) and
  # prunes the lazy_deps.py packages the install accretes at first use.
  VIRTUAL_ENV="$INSTALL/venv" "$UV" pip install -e '.[all]'
fi

# ── 4. restart services ─────────────────────────────────────────────────────
launchctl kickstart -k "gui/$(id -u)/ai.hermes.gateway"        2>/dev/null || true
launchctl kickstart -k "gui/$(id -u)/ai.hermes.gateway-studio" 2>/dev/null || true

echo "✓ deployed $(git log -1 --format='%h %s') — restart the desktop app to pick it up"
