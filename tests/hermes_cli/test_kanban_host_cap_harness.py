"""The dispatcher's host cap counts only work that occupies this host (F-040).

Harness boards (claude, codex, muse, ...) are mostly worked by external sessions
that claim cards through the CLI: those rows are ``running`` with no worker pid.
Counting them against ``kanban.max_in_progress`` blocked every Hermes spawn on
2026-10-09 (three claude-board claims held a one-slot cap). A worker the
dispatcher did spawn on a harness board (dashboard nudge, ``--board claude
dispatch``) has a pid and must still count, or the cap can be exceeded.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from hermes_cli.kanban_dispatch_scope import HARNESS_BOARDS, other_running_readonly


# -- helper level ---------------------------------------------------------------

def _board(root: Path, slug: str, claims: int = 0, workers: int = 0) -> Path:
    path = root / slug / "kanban.db"
    path.parent.mkdir(parents=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE tasks (id TEXT, status TEXT, worker_pid INTEGER)")
    conn.executemany("INSERT INTO tasks VALUES (?, 'running', NULL)", [(f"c_{slug}_{i}",) for i in range(claims)])
    conn.executemany("INSERT INTO tasks VALUES (?, 'running', ?)", [(f"w_{slug}_{i}", 4000 + i) for i in range(workers)])
    conn.commit()
    conn.close()
    return path


def _kb(root: Path, slugs):
    return SimpleNamespace(
        DEFAULT_BOARD="default",
        list_boards=lambda include_archived=False: [{"slug": s} for s in slugs],
        kanban_db_path=lambda board=None: root / board / "kanban.db",
    )


def test_harness_cli_claims_do_not_use_the_host_cap(tmp_path):
    _board(tmp_path, "acc")
    _board(tmp_path, "claude", claims=3)
    _board(tmp_path, "muse", claims=1)
    _board(tmp_path, "studio", workers=1)
    assert other_running_readonly(_kb(tmp_path, ["acc", "claude", "muse", "studio"]), "acc") == 1


def test_spawned_worker_on_a_harness_board_still_counts(tmp_path):
    _board(tmp_path, "acc")
    _board(tmp_path, "claude", claims=3, workers=1)
    assert other_running_readonly(_kb(tmp_path, ["acc", "claude"]), "acc") == 1


def test_every_harness_board_skips_claims_only(tmp_path):
    slugs = sorted(HARNESS_BOARDS)
    for slug in slugs:
        _board(tmp_path, slug, claims=2)
    _board(tmp_path, "acc")
    assert other_running_readonly(_kb(tmp_path, slugs + ["acc"]), "acc") == 0


def test_claims_on_a_non_harness_board_still_count(tmp_path):
    _board(tmp_path, "acc")
    _board(tmp_path, "studio", claims=2)
    assert other_running_readonly(_kb(tmp_path, ["acc", "studio"]), "acc") == 2


def test_unreadable_board_still_fails_closed(tmp_path):
    _board(tmp_path, "acc")
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "kanban.db").write_text("not a database")
    assert other_running_readonly(_kb(tmp_path, ["acc", "broken"]), "acc") is None


# -- dispatch level: the incident on both paths ---------------------------------

@pytest.fixture()
def home(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    for var in ("HERMES_KANBAN_HOME", "HERMES_KANBAN_DB", "HERMES_KANBAN_BOARD"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from hermes_cli import kanban_db as kb, kanban_db_connect as kbc, kanban_db_dispatch as kbd
    monkeypatch.setattr(kbd, "_system_memory_sample", lambda: {})
    monkeypatch.setattr(kbd, "_profile_exists_fn", lambda: None)
    for slug in ("acc", "claude"):
        kb.create_board(slug)
    return SimpleNamespace(kb=kb, kbc=kbc, kbd=kbd)


def _spawn(task, workspace, board=None):
    return os.getpid()


def _claude_cli_claims(h, n=3):
    with h.kbc.connect_closing(board="claude") as conn:
        for i in range(n):
            tid = h.kb.create_task(conn, title=f"claim {i}", body="x")
            conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (tid,))
            h.kb.claim_task(conn, tid)


def test_unscoped_tick_spawns_despite_harness_cli_claims(home):
    """`hermes kanban dispatch` / daemon path (count_running_tasks_other_boards)."""
    _claude_cli_claims(home)
    with home.kbc.connect_closing(board="acc") as conn:
        tid = home.kb.create_task(conn, title="work", body="x", assignee="professor")
        conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (tid,))
        result = home.kbd.dispatch_once(conn, board="acc", max_spawn=8, max_in_progress=1, spawn_fn=_spawn)
    assert [s[0] for s in result.spawned] == [tid]


def test_reviewed_tick_spawns_despite_harness_cli_claims(home):
    """The gateway's reviewed path (other_running_readonly)."""
    from hermes_cli.kanban_dispatch_scope import task_digest
    _claude_cli_claims(home)
    judge = "card-model (D-265): tier=standard model=m provider=p"
    with home.kbc.connect_closing(board="acc") as conn:
        tid = home.kb.create_task(conn, title="w", body="spec", assignee="studio", model_override="m", provider_override="p")
        conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (tid,))
        home.kb.add_comment(conn, tid, "card-model", judge)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
        entry = {"board": "acc", "approved": True, "expires_at": time.time() + 3600, "task_id": tid,
                 "task_sha256": task_digest(row), "judge_sha256": hashlib.sha256(judge.encode()).hexdigest()}
        result = home.kbd.dispatch_once(conn, board="acc", max_spawn=1, max_in_progress=1,
                                        spawn_fn=_spawn, eligibility_scope=[entry])
    assert [s[0] for s in result.spawned] == [tid]


def test_spawned_harness_worker_blocks_the_cap_on_both_paths(home):
    """A real worker on the claude board fills a one-slot cap for acc."""
    with home.kbc.connect_closing(board="claude") as conn:
        cid = home.kb.create_task(conn, title="nudged", body="x", assignee="professor")
        conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (cid,))
        nudged = home.kbd.dispatch_once(conn, board="claude", max_spawn=8, spawn_fn=_spawn)
    assert [s[0] for s in nudged.spawned] == [cid]
    with home.kbc.connect_closing(board="acc") as conn:
        tid = home.kb.create_task(conn, title="work", body="x", assignee="professor")
        conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (tid,))
        result = home.kbd.dispatch_once(conn, board="acc", max_spawn=8, max_in_progress=1, spawn_fn=_spawn)
    assert result.spawned == []
