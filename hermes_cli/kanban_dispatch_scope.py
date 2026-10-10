"""Finite, reviewed automatic-dispatch scope; never promotes or revives backlog."""
from __future__ import annotations

import hashlib
import json
import time

HARNESS_BOARDS = frozenset({"claude", "codex", "hermes", "gemini", "qwen",
                            "opencode", "kimi", "cursor-agent", "muse"})
FIELDS = ("title", "body", "assignee", "model_override", "provider_override",
          "workspace_kind", "workspace_path", "branch_name", "project_id",
          "skills", "max_runtime_seconds", "max_retries", "completion_contract",
          "tenant", "reasoning_effort", "goal_mode", "goal_max_turns")


def task_digest(row):
    return hashlib.sha256(json.dumps({k: row[k] for k in FIELDS},
                                    sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def reviewed_entries(scope, board):
    """Malformed/missing/expired scope fails closed. No implicit profile fallback."""
    if board in HARNESS_BOARDS or not isinstance(scope, list):
        return []
    entries = []
    for entry in scope:
        if not isinstance(entry, dict) or entry.get("board") != board:
            continue
        if entry.get("approved") is not True:
            continue
        expiry = entry.get("expires_at")
        if type(expiry) not in (int, float) or not time.time() < expiry:
            continue
        if not all(isinstance(entry.get(k), str) and entry[k].strip()
                   for k in ("task_id", "task_sha256", "judge_sha256")):
            continue
        entries.append(entry)
    return entries


def eligible(conn, entry):
    """Recheck immutable approval, dependencies and one-shot status at claim time."""
    if not reviewed_entries([entry], entry.get("board")):
        return False
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (entry["task_id"],)).fetchone()
    if row is None or row["status"] != "ready" or row["claim_lock"] is not None:
        return False
    if row["assignee"] not in {"studio", "professor"}:
        return False
    model, provider = row["model_override"], row["provider_override"]
    if not model or not provider:
        return False
    if "muse" in (model + " " + provider).lower():
        return False
    if task_digest(row) != entry["task_sha256"]:
        return False
    if entry.get("local_endpoint"):
        from hermes_cli import kanban_db as kb
        from hermes_cli.kanban_ready_cycle import ReadyCycle
        local = dict(endpoint=entry["local_endpoint"], provider=entry.get("local_provider"), model=entry.get("local_model"))
        if model != local["model"] or provider != local["provider"] or not ReadyCycle(kb)._route_matches(row, local):
            return False
    # A finite approval is consumed by its first attempt; no automatic retries.
    if conn.execute("SELECT 1 FROM task_runs WHERE task_id=? LIMIT 1",
                    (entry["task_id"],)).fetchone():
        return False
    if conn.execute("SELECT 1 FROM task_links l LEFT JOIN tasks p ON p.id=l.parent_id "
                    "WHERE l.child_id=? AND (p.id IS NULL OR p.status NOT IN ('done','archived')) LIMIT 1",
                    (entry["task_id"],)).fetchone():
        return False
    comments = conn.execute("SELECT body FROM task_comments WHERE task_id=?",
                            (entry["task_id"],)).fetchall()
    return any((c["body"] or "").startswith("card-model (D-265):") and
               hashlib.sha256(c["body"].encode()).hexdigest() == entry["judge_sha256"]
               for c in comments)


def other_running_readonly(kb, board):
    """Honor the shared host cap without migrating or writing other boards."""
    import sqlite3
    try:
        seen = {kb.kanban_db_path(board=board).expanduser().resolve()}
        total = 0
        for meta in kb.list_boards(include_archived=False):
            path = kb.kanban_db_path(board=meta.get("slug") or kb.DEFAULT_BOARD).expanduser().resolve()
            if path in seen or not path.exists():
                continue
            seen.add(path)
            conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
            try:
                total += conn.execute("SELECT COUNT(*) FROM tasks WHERE status='running'").fetchone()[0]
            finally:
                conn.close()
        return total
    except Exception:
        # Unknown host occupancy must not silently widen the concurrency budget.
        return None


def boards_with_running_readonly(kb):
    """Board slugs whose DB has a ``running`` task, read-only (no open/migrate of idle boards).

    Scoped dispatch must still tick these so crash/stale/max-runtime sweeps reclaim workers."""
    import sqlite3
    slugs = set()
    try:
        for meta in kb.list_boards(include_archived=False):
            slug = meta.get("slug") or kb.DEFAULT_BOARD
            path = kb.kanban_db_path(board=slug).expanduser().resolve()
            if not path.exists():
                continue
            conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
            try:
                if conn.execute("SELECT 1 FROM tasks WHERE status='running' LIMIT 1").fetchone():
                    slugs.add(slug)
            finally:
                conn.close()
    except Exception:
        pass
    return slugs
