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
