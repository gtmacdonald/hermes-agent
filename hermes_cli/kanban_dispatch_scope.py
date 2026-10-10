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
# The only assignees reviewed dispatch ever starts; kanban.dispatch_profiles can narrow them.
SCOPED_ASSIGNEES = frozenset({"studio", "professor"})


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
    if row["assignee"] not in SCOPED_ASSIGNEES:
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


def effective_scope(kanban_cfg, ready_cycle):
    """``kanban.dispatch_scope`` plus the ready cycle's selected approvals."""
    scope = kanban_cfg.get("dispatch_scope", [])
    return (scope if isinstance(scope, list) else []) + ready_cycle.scopes(kanban_cfg)


def dispatch_assignees():
    """Assignees reviewed dispatch may start in this home (fail-closed allowlist)."""
    from hermes_cli.kanban_db_dispatch import _dispatch_profile_allowlist
    from hermes_cli.profiles import normalize_profile_name
    allowlist = _dispatch_profile_allowlist(normalize_profile_name)
    return SCOPED_ASSIGNEES if allowlist is None else SCOPED_ASSIGNEES & allowlist


def admission(conn, board, kanban_cfg, kb, *, assignees=None, ready_cycle=None):
    """``(waiting, admitted)`` for one board, read-only (F-039).

    ``waiting``: sorted ids of ready, unclaimed cards reviewed dispatch could start.
    ``admitted``: the live approvals that admit one of them -- an eligible scope entry, or a
    ready-cycle approval still awaiting selection (a held receipt admits nothing).
    """
    from hermes_cli.kanban_ready_cycle import ReadyCycle, approvals, candidate
    if board in HARNESS_BOARDS:
        return [], []
    assignees = dispatch_assignees() if assignees is None else assignees
    waiting = sorted(r["id"] for r in conn.execute(
        "SELECT id, assignee FROM tasks WHERE status='ready' AND claim_lock IS NULL")
        if r["assignee"] in assignees)
    if not waiting:
        return [], []
    ready_cycle = ready_cycle or ReadyCycle(kb)
    admitted = [e for e in reviewed_entries(effective_scope(kanban_cfg, ready_cycle), board)
                if e["task_id"] in waiting and eligible(conn, e)]
    admitted += [e for e in approvals(kanban_cfg)
                 if e["board"] == board and e["task_id"] in waiting
                 and ready_cycle.pending(e) and candidate(conn, e) is not None]
    return waiting, admitted


def starved_boards_readonly(kb, kanban_cfg, *, ready_cycle=None):
    """``({board: waiting}, unreadable)``: boards whose waiting cards no live approval admits.

    Opens each board read-only without migrating it (``query_only``: a ``mode=ro`` handle
    cannot recreate a checkpointed WAL's ``-shm``). A board that cannot be read lands in
    ``unreadable`` so the caller keeps its last known state instead of flapping.
    """
    import sqlite3
    assignees = dispatch_assignees()
    starved, unreadable, seen = {}, set(), set()
    for meta in kb.list_boards(include_archived=False):
        slug = meta.get("slug") or kb.DEFAULT_BOARD
        if slug in HARNESS_BOARDS:
            continue
        try:
            path = kb.kanban_db_path(board=slug).expanduser().resolve()
            if path in seen or not path.exists():
                continue
            seen.add(path)
            conn = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True)
            try:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA query_only=ON")
                waiting, admitted = admission(conn, slug, kanban_cfg, kb, assignees=assignees,
                                              ready_cycle=ready_cycle)
            finally:
                conn.close()
        except Exception:
            unreadable.add(slug)
            continue
        if waiting and not admitted:
            starved[slug] = len(waiting)
    return starved, unreadable
