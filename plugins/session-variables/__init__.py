"""session-variables plugin — inject per-session user-configurable variables.

Wires one hook:

* ``pre_llm_call`` — reads ``<HERMES_HOME>/session_vars/<session_id>.json``,
  formats it as a context block, and returns it for injection into the
  current turn's user message (never the system prompt — preserves prompt
  caching).

**On-change only.** A process-local dict tracks the last-seen file mtime per
session. When the mtime is unchanged (or the file is absent), the hook
returns ``None`` and adds zero tokens. When the file is new or modified,
the full current variable set is injected. The model sees the delta
because prior injections ride the ``api_content`` sidecar in conversation
history.

Error handling: if the JSON is corrupt, the hook injects an error block
(naming the parse error) instead of failing silently — the user needs to
know their variables are malformed.

Storage layout::

    ~/.hermes/session_vars/<session_id>.json

Example file::

    {
        "project": "auth refactor",
        "timezone": "America/New_York",
        "sprint": "Sprint 14",
        "notes": "API v2 migration in progress"
    }
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)

# Process-local: session_id -> last-seen mtime (float).
# Reset on process restart, which is fine — first turn after restart
# re-injects if the file exists.
_last_mtime: Dict[str, float] = {}


def _vars_path(session_id: str) -> Path:
    """Return the JSON path for *session_id* under the active profile."""
    return get_hermes_home() / "session_vars" / f"{session_id}.json"


def _format_variables(data: Dict[str, Any]) -> str:
    """Render the variable dict as a fenced context block."""
    lines = ["<session-variables>"]
    for key in sorted(data.keys()):
        lines.append(f"{key}: {data[key]}")
    lines.append("</session-variables>")
    return "\n".join(lines)


def _on_pre_llm_call(
    session_id: str = "",
    **_: Any,
) -> Optional[Dict[str, str]]:
    """Inject session variables on first sight or after a file change.

    Returns ``{"context": "..."}`` when variables should be injected,
    ``None`` to stay silent (no change since last injection).
    """
    if not session_id:
        return None

    path = _vars_path(session_id)

    # File absent — nothing to inject. We don't track this as a "change"
    # (deletion → silence), so the model retains whatever it last saw via
    # the api_content sidecar in conversation history.
    if not path.is_file():
        return None

    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None

    # On-change gate: skip if we've already injected this exact mtime.
    if _last_mtime.get(session_id) == mtime:
        return None

    # File is new or changed — read it.
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        # Corrupt JSON — tell the model (and thus the user) instead of
        # silently dropping the variables.
        _last_mtime[session_id] = mtime  # mark as seen so we don't spam
        return {
            "context": (
                f"<session-variables ERROR>\n"
                f"Variables file is corrupt and could not be parsed: {exc}\n"
                f"Path: {path}\n"
                f"</session-variables ERROR>"
            )
        }
    except Exception as exc:
        logger.warning("session-variables: failed to read %s: %s", path, exc)
        return None

    if not isinstance(data, dict) or not data:
        # Empty or wrong shape — mark seen, stay silent.
        _last_mtime[session_id] = mtime
        return None

    _last_mtime[session_id] = mtime
    return {"context": _format_variables(data)}


# ---------------------------------------------------------------------------
# CRUD helpers — used by the /var slash command across CLI, gateway, and TUI.
# ---------------------------------------------------------------------------


def _load_raw(session_id: str) -> dict:
    """Load and return the variables dict for *session_id* (empty if absent)."""
    path = _vars_path(session_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return {}


def _save_raw(session_id: str, data: dict) -> None:
    """Write the variables dict, creating the directory if needed."""
    path = _vars_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def set_var(session_id: str, key: str, value: str) -> str:
    """Set a single variable. Returns a human-readable status string."""
    if not key:
        return "Variable name is required."
    data = _load_raw(session_id)
    data[key] = value
    _save_raw(session_id, data)
    return f"Set variable '{key}' = '{value}'."


def del_var(session_id: str, key: str) -> str:
    """Delete a single variable. Returns a human-readable status string."""
    data = _load_raw(session_id)
    if key not in data:
        return f"Variable '{key}' is not set."
    del data[key]
    if data:
        _save_raw(session_id, data)
    else:
        # Remove the file entirely when the last key is deleted so the
        # hook stays silent instead of injecting an empty block.
        try:
            _vars_path(session_id).unlink()
        except FileNotFoundError:
            pass
    return f"Deleted variable '{key}'."


def clear_vars(session_id: str) -> str:
    """Remove all variables for *session_id*."""
    path = _vars_path(session_id)
    try:
        path.unlink()
        return "Cleared all session variables."
    except FileNotFoundError:
        return "No variables to clear."


def list_vars(session_id: str) -> str:
    """Return a formatted listing of all variables, or a 'none set' message."""
    data = _load_raw(session_id)
    if not data:
        return "No session variables set. Usage: /var set <key> <value>"
    lines = [f"Session variables ({len(data)}):"]
    for k in sorted(data.keys()):
        lines.append(f"  {k} = {data[k]}")
    lines.append("")
    lines.append(f"Path: {_vars_path(session_id)}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API — resilient import for callers (CLI, gateway, TUI).
# ---------------------------------------------------------------------------

def get_api():
    """Return ``(set_var, del_var, clear_vars, list_vars)`` from this module.

    Callers import via ``hermes_plugins.session_variables`` at runtime, but
    the module also needs to work during development and tests where the
    PluginManager hasn't created the namespace. This helper tries both paths.
    """
    import sys
    for mod_name in ("hermes_plugins.session_variables", "plugins.session_variables"):
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, "set_var"):
            return mod.set_var, mod.del_var, mod.clear_vars, mod.list_vars
    # Fallback: import from this very module (when loaded directly)
    return set_var, del_var, clear_vars, list_vars


def register(ctx) -> None:
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
