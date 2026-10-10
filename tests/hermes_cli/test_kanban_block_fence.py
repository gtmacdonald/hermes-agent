"""block and schedule clear a running card's claim, so they take the same live-claim fence
as complete and request-review: the holder proves ownership (worker run id, or ``--claimer``
for a named claim) or the caller passes ``--force``. Before this, any session could block or
schedule a card another harness held, ending its run with no override asked for.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from hermes_cli import kanban as kc
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli import kanban_db_dispatch as kbd

OWNER = "claude:905f3fd6"
OTHER = "codex:01a10973"


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for var in ("HERMES_KANBAN_CLAIMER", "HERMES_KANBAN_CLAIM_LOCK",
                "HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID"):
        monkeypatch.delenv(var, raising=False)
    kb._INITIALIZED_PATHS.clear()
    kb.init_db()
    return home


def _claimed(claimer: str = OWNER) -> str:
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="held card", body="x")
    kc.run_slash(f"claim {tid} --claimer {claimer}")
    return tid


def _task(tid: str) -> kb.Task:
    with kbc.connect() as conn:
        return kb.get_task(conn, tid)


def _comments(tid: str) -> list[str]:
    with kbc.connect() as conn:
        return [c.body for c in kb.list_comments(conn, tid)]


@pytest.mark.parametrize("verb, landed", [("block", "blocked"), ("schedule", "scheduled")])
def test_named_claim_fences_block_and_schedule(kanban_home, verb, landed):
    tid = _claimed()

    out = kc.run_slash(f"{verb} {tid} mine now --claimer {OTHER}")
    assert f"claimed by {OWNER}" in out, out
    assert _task(tid).status == "running" and _task(tid).claim_lock == OWNER
    # A refused verb leaves no reason comment on a card someone else holds.
    assert _comments(tid) == []

    out = kc.run_slash(f"{verb} {tid} waiting on input --claimer {OWNER}")
    assert _task(tid).status == landed, out
    assert any("waiting on input" in body for body in _comments(tid))


@pytest.mark.parametrize("verb, landed", [("block", "blocked"), ("schedule", "scheduled")])
def test_force_overrides_the_block_fence(kanban_home, verb, landed):
    tid = _claimed()
    out = kc.run_slash(f"{verb} {tid} operator override --force")
    assert _task(tid).status == landed, out


def test_live_worker_claim_fences_library_block(kanban_home):
    """The fence lives in kanban_db: a caller without the worker's run id cannot block it."""
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="worker card", assignee="coder")
        claimed = kb.claim_task(conn, tid, claimer=kb._claimer_id())
        kbd._set_worker_pid(conn, tid, os.getpid())
        with pytest.raises(kb.LiveClaimError):
            kb.block_task(conn, tid, reason="not mine")
        with pytest.raises(kb.LiveClaimError):
            kb.schedule_task(conn, tid, reason="not mine")
        assert kb.get_task(conn, tid).status == "running"
        assert kb.block_task(conn, tid, reason="mine", expected_run_id=claimed.current_run_id) is True


def test_anonymous_claim_without_a_worker_still_blocks(kanban_home):
    """Unchanged: a host-local claim with no worker process protects no run."""
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="cli card", body="x")
        assert kb.claim_task(conn, tid, claimer=kb._claimer_id()) is not None
        assert kb.block_task(conn, tid, reason="stuck") is True


def test_block_by_the_holder_names_the_holder_as_actor(kanban_home):
    """``block --claimer X`` attributes the ``blocked`` event to X, like claim, heartbeat and
    complete do, not to the short-lived CLI process's host:pid."""
    tid = _claimed()
    kc.run_slash(f"block {tid} waiting on input --claimer {OWNER}")
    with kbc.connect() as conn:
        blocked = [e for e in kb.list_events(conn, tid) if e.kind == "blocked"]
    assert blocked and blocked[-1].payload.get("actor") == OWNER


def _park_untyped(tid: str) -> None:
    """Leave ``tid`` the way the failure breaker does: ``blocked``, no ``block_kind``,
    no live run, no ``blocked`` event."""
    with kbc.connect() as conn, kb.write_txn(conn):
        conn.execute(
            "UPDATE tasks SET status = 'blocked', block_kind = NULL, current_run_id = NULL, "
            "claim_lock = NULL, claim_expires = NULL, worker_pid = NULL WHERE id = ?", (tid,))


def test_in_place_classification_names_the_claimer_as_actor(kanban_home):
    """Classifying a breaker-parked card in place (``block --kind K --claimer X`` on an
    untyped ``blocked`` card) attributes its ``blocked`` event to X too, not to host:pid."""
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="parked card", body="x")
    _park_untyped(tid)
    out = kc.run_slash(f"block {tid} needs a decision --kind needs_input --claimer {OWNER}")
    assert _task(tid).block_kind == "needs_input", out
    with kbc.connect() as conn:
        blocked = [e for e in kb.list_events(conn, tid) if e.kind == "blocked"]
    assert len(blocked) == 1 and blocked[0].payload.get("classified_in_place") is True
    assert blocked[0].payload.get("actor") == OWNER


def test_in_place_classification_library_actor(kanban_home):
    """The library forwards ``actor`` on the in-place branch; without one it still resolves
    an actor (host:pid here) rather than leaving the event unattributed."""
    with kbc.connect() as conn:
        named = kb.create_task(conn, title="named", body="x")
        anon = kb.create_task(conn, title="anon", body="x")
    _park_untyped(named)
    _park_untyped(anon)
    with kbc.connect() as conn:
        assert kb.block_task(conn, named, reason="r", kind="capability", actor=OWNER) is True
        assert kb.block_task(conn, anon, reason="r", kind="capability") is True
        by = {t: [e for e in kb.list_events(conn, t) if e.kind == "blocked"][-1].payload["actor"]
              for t in (named, anon)}
    assert by[named] == OWNER
    assert by[anon] == kb._claimer_id()


def test_routed_block_events_always_name_an_actor(kanban_home):
    """``dependency_wait`` and ``block_loop_detected`` (the other events block_task emits)
    name the explicit actor when given and resolve one otherwise, like ``blocked`` does."""
    with kbc.connect() as conn:
        parent = kb.create_task(conn, title="parent", body="x")
        waiter = kb.create_task(conn, title="waiter", body="x", parents=[parent])
        looper = kb.create_task(conn, title="looper", body="x")
        with kb.write_txn(conn):
            conn.execute("UPDATE tasks SET status = 'ready' WHERE id = ?", (waiter,))
        assert kb.block_task(conn, waiter, reason="needs parent", kind="dependency") is True
        assert kb.block_task(conn, looper, reason="first", kind="needs_input", actor=OWNER) is True
        assert kb.unblock_task(conn, looper) is True
        assert kb.block_task(conn, looper, reason="again", kind="needs_input") is True
        wait = [e for e in kb.list_events(conn, waiter) if e.kind == "dependency_wait"][-1]
        loop = [e for e in kb.list_events(conn, looper) if e.kind == "block_loop_detected"][-1]
        assert kb.get_task(conn, looper).status == "triage"
    assert wait.payload.get("actor") == kb._claimer_id()
    assert loop.payload.get("actor") == kb._claimer_id()
    explicit = kb._route_block("dependency", "r", "ready", prev_kind=None, prev_recurrences=0, actor=OWNER)
    assert explicit[4]["actor"] == OWNER
