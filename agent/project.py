"""Mid-session project/topic switching: change the working directory and
optionally label the current topic, then produce a one-shot note that tells
the model what changed.

The note is injected as a prefix on the NEXT user message (same pattern as
``_pending_model_switch_note`` and ``_pending_skills_reload_note``), so it
preserves prompt caching and message-role alternation — the system prompt's
``Current working directory:`` line stays in the cached prefix; the note rides
the new turn.

Three surfaces call into ``apply_project_change``:

* **CLI** (``cli.py``) — ``os.chdir`` + ``TERMINAL_CWD`` + queue the note.
* **TUI / desktop** (``tui_gateway``) — ``_set_session_cwd`` + queue the note
  on ``session["_pending_project_note"]``.
* **Gateway** (``gateway/run.py``) — ``set_session_cwd`` contextvar +
  ``os.environ["TERMINAL_CWD"]`` + queue the note on the session state.

Usage::

    from agent.project import apply_project_change, take_project_note

    note = apply_project_change("/path/to/project", topic="auth refactor")
    # … store `note` for the next user message …
    # at turn time:
    text = take_project_note(session_or_self)  # returns + clears the note
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Optional

# Attribute / key names — kept constant so every surface reads and clears the
# same slot.
PENDING_NOTE_ATTR = "_pending_project_note"
PENDING_TOPIC_ATTR = "_pending_project_topic"


def _resolve_cwd(raw: str) -> str:
    """Expand ``~`` and return an absolute, existing directory path.

    Raises ``ValueError`` if the directory does not exist.
    """
    resolved = os.path.abspath(os.path.expanduser(str(raw).strip()))
    if not os.path.isdir(resolved):
        raise ValueError(f"working directory does not exist: {raw}")
    return resolved


def _detect_project_name(cwd: str) -> str:
    """Best-effort short label for ``cwd`` — the directory basename."""
    try:
        return Path(cwd).name or cwd
    except Exception:
        return cwd


def _git_branch_for_cwd(cwd: str) -> str:
    """Best-effort git branch name for ``cwd``; empty string on any failure."""
    try:
        import subprocess

        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
            if branch and branch != "HEAD":
                return branch
    except Exception:
        pass
    return ""


def build_project_note(
    *,
    old_cwd: str,
    new_cwd: str,
    old_topic: Optional[str],
    new_topic: Optional[str],
) -> str:
    """Build the one-shot note that tells the model what changed.

    The note is prepended to the next user message (API-local, never persisted)
    so the model knows the cwd/project and/or topic label changed mid-session
    without the system prompt being rewritten (which would break the prefix
    cache).
    """
    parts: list[str] = ["[Project context changed:"]

    if new_cwd != old_cwd:
        parts.append(f"working directory changed from {old_cwd} to {new_cwd}.")
        project_name = _detect_project_name(new_cwd)
        branch = _git_branch_for_cwd(new_cwd)
        desc_parts = [f'project "{project_name}"']
        if branch:
            desc_parts.append(f"branch {branch}")
        parts.append(f"Now in {' '.join(desc_parts)}.")
        parts.append("Use the new working directory for all file and terminal operations.")

    if new_topic != old_topic:
        if new_topic:
            parts.append(f"Topic label set to: \"{new_topic}\".")
        elif old_topic:
            parts.append("Topic label cleared.")

    parts.append("]")
    return " ".join(parts)


def apply_project_change(
    new_cwd: str,
    *,
    topic: Optional[str] = None,
    old_cwd: Optional[str] = None,
    old_topic: Optional[str] = None,
    session_key: Optional[str] = None,
) -> str:
    """Change the working directory for a CLI/local session and return the note.

    For CLI/local backends this ``os.chdir()``s the process and sets
    ``TERMINAL_CWD`` so the terminal tool, code-exec tool, and relative-path
    resolution all land in the new place (same mechanism as
    ``_restore_session_cwd``). For gateway/TUI sessions the caller should use
    ``apply_project_change_session`` instead, which uses the contextvar-based
    ``set_session_cwd`` rather than mutating the process cwd.

    Args:
        new_cwd: The directory to switch to (``~`` expanded).
        topic: Optional topic label for the project.
        old_cwd: Previous cwd (auto-detected if ``None``).
        old_topic: Previous topic label (``None`` = no prior topic).
        session_key: If given, re-anchors the terminal tool's per-session
            env overrides so the live shell follows the move.

    Returns:
        The one-shot note string to prepend to the next user message.
    """
    resolved = _resolve_cwd(new_cwd)
    if old_cwd is None:
        try:
            old_cwd = os.getcwd()
        except OSError:
            old_cwd = str(Path.home())
    elif not os.path.isabs(old_cwd):
        old_cwd = os.path.abspath(old_cwd)

    if resolved != old_cwd:
        try:
            os.chdir(resolved)
        except OSError as exc:
            raise ValueError(f"could not change to {resolved}: {exc}") from exc
        os.environ["TERMINAL_CWD"] = resolved

    # Re-anchor the terminal tool's per-session cwd override so the live
    # shell (if one exists for this session) follows the move.
    if session_key:
        try:
            from tools.terminal_tool import register_task_env_overrides

            register_task_env_overrides(session_key, {"cwd": resolved})
        except Exception:
            pass

    return build_project_note(
        old_cwd=old_cwd,
        new_cwd=resolved,
        old_topic=old_topic,
        new_topic=topic,
    )


def apply_project_change_session(
    session: dict,
    new_cwd: str,
    *,
    topic: Optional[str] = None,
    session_key: Optional[str] = None,
) -> str:
    """Change cwd for a TUI/gateway session dict and return the note.

    Uses ``set_session_cwd`` (contextvar) rather than ``os.chdir``, matching
    how the TUI gateway's ``_set_session_cwd`` works. Also updates the
    session dict's ``cwd``/``explicit_cwd`` fields, persists to the session DB
    if available, and re-anchors the terminal tool.

    The caller is responsible for storing the returned note on the session
    (typically ``session[PENDING_NOTE_ATTR] = note``) and for clearing it at
    turn time via ``take_project_note``.
    """
    resolved = _resolve_cwd(new_cwd)
    old_cwd = str(session.get("cwd") or "")
    if not old_cwd:
        try:
            old_cwd = os.getcwd()
        except OSError:
            old_cwd = str(Path.home())
    elif not os.path.isabs(old_cwd):
        old_cwd = os.path.abspath(old_cwd)

    old_topic = session.get(PENDING_TOPIC_ATTR) or ""

    # Update the session dict
    session["cwd"] = resolved
    session["explicit_cwd"] = True
    session["cwd_from_settle"] = False
    if topic is not None:
        session[PENDING_TOPIC_ATTR] = topic

    # Pin the contextvar so resolve_agent_cwd() follows
    try:
        from agent.runtime_cwd import set_session_cwd

        set_session_cwd(resolved)
    except Exception:
        pass

    # Re-anchor the terminal tool
    key = session_key or session.get("session_key") or ""
    if key:
        try:
            from tools.terminal_tool import register_task_env_overrides

            register_task_env_overrides(key, {"cwd": resolved})
        except Exception:
            pass

    # Persist to session DB
    db_session_key = session.get("session_key", "")
    if db_session_key:
        try:
            from tui_gateway.server import _session_db, _persist_session_git_meta

            with _session_db(session) as db:
                if db is not None:
                    db.update_session_cwd(db_session_key, resolved)
            _persist_session_git_meta(session, resolved)
        except Exception:
            # Best-effort — the in-memory state is already correct.
            pass

    return build_project_note(
        old_cwd=old_cwd,
        new_cwd=resolved,
        old_topic=old_topic or None,
        new_topic=topic,
    )


def queue_project_note(obj: Any, note: str) -> None:
    """Store ``note`` on ``obj`` (CLI instance or session dict) for the next turn."""
    setattr(obj, PENDING_NOTE_ATTR, note) if not isinstance(obj, dict) else obj.__setitem__(PENDING_NOTE_ATTR, note)


def take_project_note(obj: Any) -> str:
    """Retrieve and clear the pending project note from ``obj``.

    Returns an empty string if no note is queued. Works for both CLI instances
    (``getattr``/``setattr``) and session dicts (``get``/``pop``).
    """
    if isinstance(obj, dict):
        note = obj.pop(PENDING_NOTE_ATTR, "")
    else:
        note = getattr(obj, PENDING_NOTE_ATTR, None) or ""
        if note:
            try:
                setattr(obj, PENDING_NOTE_ATTR, None)
            except Exception:
                pass
    return note or ""


def get_project_topic(obj: Any) -> str:
    """Return the current topic label for ``obj``, or empty string if unset."""
    if isinstance(obj, dict):
        return str(obj.get(PENDING_TOPIC_ATTR) or "")
    return str(getattr(obj, PENDING_TOPIC_ATTR, None) or "")
