"""Who did it: the ``actor`` on claim-lifecycle events, ``complete --force --reason``,
and the refusal text for a named claim (audit F-006, stacked on the named-claim core).

Events carried no actor, so the board could say a card was claimed, heartbeated,
completed, reclaimed or blocked but not by whom, and a comment's author defaulted to
the Hermes profile even when an external harness wrote it. The actor resolves with the
same precedence as the claim fence: an explicit ``--claimer``, then the worker's
``HERMES_KANBAN_CLAIM_LOCK``, then ``HERMES_KANBAN_CLAIMER``, then ``host:pid``. It goes
in the event payload; no schema change.
"""

from __future__ import annotations

import json
import os
import time
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


def _ready_task() -> str:
    with kbc.connect() as conn:
        return kb.create_task(conn, title="harness card", body="do the thing")


def _last_event(tid: str, kind: str) -> kb.Event:
    with kbc.connect() as conn:
        events = [e for e in kb.list_events(conn, tid) if e.kind == kind]
    assert events, f"no {kind} event on {tid}"
    return events[-1]


def _comments(tid: str) -> list[kb.Comment]:
    with kbc.connect() as conn:
        return kb.list_comments(conn, tid)


# ---------------------------------------------------------------------------
# resolve_actor: the one precedence every event uses
# ---------------------------------------------------------------------------

def test_resolve_actor_precedence(kanban_home, monkeypatch):
    assert kb.resolve_actor() == kb._claimer_id()
    monkeypatch.setenv("HERMES_KANBAN_CLAIMER", OWNER)
    assert kb.resolve_actor() == OWNER
    monkeypatch.setenv("HERMES_KANBAN_CLAIM_LOCK", "worker-host:4242")
    assert kb.resolve_actor() == "worker-host:4242"
    assert kb.resolve_actor(OTHER) == OTHER


# ---------------------------------------------------------------------------
# actor on claimed / heartbeat / completed / reclaimed / blocked
# ---------------------------------------------------------------------------

def test_claimed_event_names_the_claimer(kanban_home):
    tid = _ready_task()
    kc.run_slash(f"claim {tid} --claimer {OWNER}")
    assert _last_event(tid, "claimed").payload["actor"] == OWNER


def test_claimed_event_falls_back_to_host_pid(kanban_home):
    tid = _ready_task()
    kc.run_slash(f"claim {tid}")
    payload = _last_event(tid, "claimed").payload
    assert payload["actor"] == kb._claimer_id() == payload["lock"]


def test_heartbeat_event_names_the_holder(kanban_home):
    tid = _ready_task()
    kc.run_slash(f"claim {tid} --claimer {OWNER}")
    kc.run_slash(f"heartbeat {tid} --claimer {OWNER}")
    assert _last_event(tid, "heartbeat").payload["actor"] == OWNER


def test_completed_event_names_the_holder(kanban_home, monkeypatch):
    tid = _ready_task()
    monkeypatch.setenv("HERMES_KANBAN_CLAIMER", OWNER)
    kc.run_slash(f"claim {tid}")
    out = kc.run_slash(f"complete {tid} --result 'shipped'")
    assert "Completed" in out, out
    payload = _last_event(tid, "completed").payload
    assert payload["actor"] == OWNER
    assert "forced_reason" not in payload


def test_manual_reclaim_event_names_who_reclaimed(kanban_home, monkeypatch):
    tid = _ready_task()
    kc.run_slash(f"claim {tid} --claimer {OWNER}")
    monkeypatch.setenv("HERMES_KANBAN_CLAIMER", OTHER)
    out = kc.run_slash(f"reclaim {tid} --reason 'owner went quiet'")
    assert "Reclaimed" in out, out
    payload = _last_event(tid, "reclaimed").payload
    assert payload["actor"] == OTHER
    assert payload["prev_lock"] == OWNER


def test_stale_sweep_reclaimed_event_names_the_sweeper(kanban_home):
    tid = _ready_task()
    kc.run_slash(f"claim {tid} --claimer {OWNER}")
    with kbc.connect() as conn:
        conn.execute("UPDATE tasks SET claim_expires = ? WHERE id = ?", (int(time.time()) - 5, tid))
        conn.commit()
        assert kb.release_stale_claims(conn) == 1
    payload = _last_event(tid, "reclaimed").payload
    assert payload["actor"] == kb._claimer_id()
    assert payload["stale_lock"] == OWNER


def test_blocked_event_names_the_blocker(kanban_home, monkeypatch):
    tid = _ready_task()
    monkeypatch.setenv("HERMES_KANBAN_CLAIMER", OWNER)
    out = kc.run_slash(f"block {tid} waiting on Greg")
    assert "Blocked" in out, out
    assert _last_event(tid, "blocked").payload["actor"] == OWNER


def test_created_blocked_keeps_its_creator_as_actor(kanban_home, monkeypatch):
    """The ``initial_status`` blocked event already names its creator; it is not overwritten."""
    monkeypatch.setenv("HERMES_KANBAN_CLAIMER", OWNER)
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="parked", initial_status="blocked", created_by="greg")
    assert _last_event(tid, "blocked").payload["actor"] == "greg"


# ---------------------------------------------------------------------------
# comment author
# ---------------------------------------------------------------------------

def test_comment_author_defaults_to_the_claimer(kanban_home, monkeypatch):
    tid = _ready_task()
    monkeypatch.setenv("HERMES_KANBAN_CLAIMER", OWNER)
    kc.run_slash(f"comment {tid} picked this up")
    assert _comments(tid)[-1].author == OWNER


def test_explicit_comment_author_wins_over_the_claimer(kanban_home, monkeypatch):
    tid = _ready_task()
    monkeypatch.setenv("HERMES_KANBAN_CLAIMER", OWNER)
    kc.run_slash(f"comment {tid} --author greg note from the desk")
    assert _comments(tid)[-1].author == "greg"


def test_comment_author_without_claimer_is_the_profile(kanban_home):
    tid = _ready_task()
    kc.run_slash(f"comment {tid} plain note")
    assert _comments(tid)[-1].author == kc._profile_author()


def test_block_reason_comment_author_matches_the_blocked_actor(kanban_home, monkeypatch):
    tid = _ready_task()
    monkeypatch.setenv("HERMES_KANBAN_CLAIMER", OWNER)
    out = kc.run_slash(f"block {tid} waiting on Greg")
    assert "Blocked" in out, out
    comment = _comments(tid)[-1]
    assert comment.body == "BLOCKED: waiting on Greg"
    assert comment.author == OWNER == _last_event(tid, "blocked").payload["actor"]


def test_schedule_reason_comment_author_is_the_claimer(kanban_home, monkeypatch):
    tid = _ready_task()
    monkeypatch.setenv("HERMES_KANBAN_CLAIMER", OWNER)
    out = kc.run_slash(f"schedule {tid} after the Monday release")
    assert "Scheduled" in out, out
    comment = _comments(tid)[-1]
    assert comment.body == "SCHEDULED: after the Monday release"
    assert comment.author == OWNER


@pytest.mark.parametrize("verb, prefix", [("block", "BLOCKED"), ("schedule", "SCHEDULED")])
def test_block_and_schedule_comment_author_without_claimer_is_the_profile(kanban_home, verb, prefix):
    tid = _ready_task()
    kc.run_slash(f"{verb} {tid} plain reason")
    comment = _comments(tid)[-1]
    assert comment.body == f"{prefix}: plain reason"
    assert comment.author == kc._profile_author()


# ---------------------------------------------------------------------------
# complete --force --reason
# ---------------------------------------------------------------------------

def test_force_with_reason_records_it(kanban_home):
    tid = _ready_task()
    kc.run_slash(f"claim {tid} --claimer {OWNER}")
    out = kc.run_slash(
        f"complete {tid} --result 'closed by operator' --claimer {OTHER} "
        f"--force --reason 'owner session crashed'")
    assert "Completed" in out, out
    payload = _last_event(tid, "completed").payload
    assert payload["forced"] is True
    assert payload["forced_reason"] == "owner session crashed"
    assert payload["actor"] == OTHER


def test_force_without_reason_warns_but_completes(kanban_home):
    """Bare ``--force`` predates the reason flag and existing callers rely on it: warn, do not refuse."""
    tid = _ready_task()
    kc.run_slash(f"claim {tid} --claimer {OWNER}")
    out = kc.run_slash(f"complete {tid} --result 'operator' --force")
    assert "Completed" in out, out
    assert "--reason" in out
    payload = _last_event(tid, "completed").payload
    assert payload["forced"] is True
    assert payload.get("forced_reason") is None


def test_reason_without_force_is_refused(kanban_home):
    tid = _ready_task()
    out = kc.run_slash(f"complete {tid} --result 'x' --reason 'why'")
    assert "--force" in out
    with kbc.connect() as conn:
        assert kb.get_task(conn, tid).status == "ready"


# ---------------------------------------------------------------------------
# a dispatched Hermes worker still completes its own claimed card
# ---------------------------------------------------------------------------

def _dispatched_worker_card(monkeypatch) -> tuple[str, str, int]:
    """A card claimed the way the dispatcher claims it, with this process standing in
    for the spawned worker (alive, fingerprinted) and the worker's env pinned."""
    lock = kb._claimer_id()
    with kbc.connect() as conn:
        tid = kb.create_task(conn, title="dispatched", assignee="coder")
        assert kb.claim_task(conn, tid, claimer=lock) is not None
        kbd._set_worker_pid(conn, tid, os.getpid())
        run_id = kb._current_run_id(conn, tid)
    monkeypatch.setenv("HERMES_KANBAN_CLAIM_LOCK", lock)
    monkeypatch.setenv("HERMES_KANBAN_TASK", tid)
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", str(run_id))
    return tid, lock, run_id


def test_dispatched_worker_completes_its_own_card_via_cli(kanban_home, monkeypatch):
    # Even with a harness claimer in the env, the worker's own lock is its identity.
    monkeypatch.setenv("HERMES_KANBAN_CLAIMER", OWNER)
    tid, lock, _ = _dispatched_worker_card(monkeypatch)
    out = kc.run_slash(f"complete {tid} --result 'worker done'")
    assert "Completed" in out, out
    assert _last_event(tid, "completed").payload["actor"] == lock


def test_dispatched_worker_completes_its_own_card_via_tool(kanban_home, monkeypatch):
    from tools import kanban_tools as kt

    tid, lock, _ = _dispatched_worker_card(monkeypatch)
    out = json.loads(kt._handle_complete({"task_id": tid, "summary": "worker done"}))
    assert not out.get("error"), out
    with kbc.connect() as conn:
        assert kb.get_task(conn, tid).status == "done"
    assert _last_event(tid, "completed").payload["actor"] == lock


def test_dispatched_worker_card_still_fences_other_callers(kanban_home, monkeypatch):
    tid, _, _ = _dispatched_worker_card(monkeypatch)
    for var in ("HERMES_KANBAN_CLAIM_LOCK", "HERMES_KANBAN_TASK", "HERMES_KANBAN_RUN_ID"):
        monkeypatch.delenv(var)
    out = kc.run_slash(f"complete {tid} --result 'not mine' --claimer {OTHER}")
    assert "live worker" in out, out
    with kbc.connect() as conn:
        assert kb.get_task(conn, tid).status == "running"


# ---------------------------------------------------------------------------
# refusal text names the holder of a named claim
# ---------------------------------------------------------------------------

def test_live_claim_error_carries_the_holder(kanban_home):
    tid = _ready_task()
    kc.run_slash(f"claim {tid} --claimer {OWNER}")
    with kbc.connect() as conn, pytest.raises(kb.LiveClaimError) as err:
        kb.complete_task(conn, tid, result="not mine")
    assert err.value.holder == OWNER
    assert OWNER in str(err.value)
    assert "live worker" not in str(err.value)


def test_tool_refusal_names_the_named_claim_holder(kanban_home):
    from tools import kanban_tools as kt

    tid = _ready_task()
    kc.run_slash(f"claim {tid} --claimer {OWNER}")
    out = json.loads(kt._handle_complete({"task_id": tid, "summary": "orchestrator says done"}))
    assert OWNER in out["error"], out
    assert "Wait for the worker" not in out["error"]
    with kbc.connect() as conn:
        assert kb.get_task(conn, tid).status == "running"
