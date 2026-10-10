"""Named claims: an external harness (a Claude, Codex or Muse session) claims a card as
``<kind>:<id>``, renews it with ``heartbeat``, and is the only caller that can close it.

Before this, ``hermes kanban claim`` recorded ``host:<pid of the CLI process>`` — a process
that exited as soon as the command returned — so no later command could prove ownership:
``heartbeat`` touched ``last_heartbeat_at`` but never extended ``claim_expires`` (the stale
sweep reclaimed the card after the TTL and booked a failure), and ``_claim_is_live``
required a worker pid, so any session could complete the claimed card.

A host-local ``host:pid`` claim keeps its upstream meaning (liveness is the process; a
claim without a live worker fences nothing, see test_kanban_complete_live_claim_guard).
A named claim has no process to check, so its lease — ``claim_expires`` — is the authority.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from hermes_cli import kanban as kc
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc

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


def _task(tid: str) -> kb.Task:
    with kbc.connect() as conn:
        return kb.get_task(conn, tid)


def _set_claim_expires(tid: str, expires: int) -> None:
    with kbc.connect() as conn:
        conn.execute("UPDATE tasks SET claim_expires = ? WHERE id = ?", (expires, tid))
        conn.commit()


# ---------------------------------------------------------------------------
# claim: identity
# ---------------------------------------------------------------------------

def test_claim_records_the_named_claimer(kanban_home):
    tid = _ready_task()
    out = kc.run_slash(f"claim {tid} --claimer {OWNER}")
    assert f"Claimed {tid}" in out, out
    task = _task(tid)
    assert task.status == "running"
    assert task.claim_lock == OWNER


def test_claim_takes_the_claimer_from_the_environment(kanban_home, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_CLAIMER", OWNER)
    tid = _ready_task()
    kc.run_slash(f"claim {tid}")
    assert _task(tid).claim_lock == OWNER


@pytest.mark.parametrize("bad", ["claude", "Claude:abc", "claude:", ":abc", "claude:a b"])
def test_claim_refuses_a_malformed_claimer(kanban_home, bad):
    tid = _ready_task()
    out = kc.run_slash(f"claim {tid} --claimer '{bad}'")
    assert "claimer" in out.lower(), out
    task = _task(tid)
    assert task.status == "ready" and task.claim_lock is None


@pytest.mark.parametrize("bad", ["player1:4242", "player1:abc", "bbctl:player1"])
def test_claim_refuses_a_claimer_that_names_this_host(kanban_home, monkeypatch, bad):
    """A named claim must not look like a host-local worker lock, or the stale sweep
    and the live-claim fence would judge it by a pid that is not its process. A
    lowercase hostname passes the <kind>:<id> regex, so the host check must catch it."""
    monkeypatch.setattr(kb, "_host_prefix", lambda: "player1:")
    tid = _ready_task()
    out = kc.run_slash(f"claim {tid} --claimer '{bad}'")
    assert "names this host" in out, out
    assert _task(tid).claim_lock is None


# ---------------------------------------------------------------------------
# heartbeat: renewal
# ---------------------------------------------------------------------------

def test_heartbeat_renews_a_named_claim(kanban_home):
    tid = _ready_task()
    kc.run_slash(f"claim {tid} --claimer {OWNER}")
    _set_claim_expires(tid, int(time.time()) + 60)  # 14 minutes have gone by

    out = kc.run_slash(f"heartbeat {tid} --claimer {OWNER}")
    assert f"Heartbeat recorded for {tid}" in out, out
    left = _task(tid).claim_expires - int(time.time())
    assert left > kb.DEFAULT_CLAIM_TTL_SECONDS - 30
    with kbc.connect() as conn:
        run = kb.latest_run(conn, tid)
    assert run.claim_expires == _task(tid).claim_expires


def test_heartbeat_ttl_sets_the_renewal_length(kanban_home):
    tid = _ready_task()
    kc.run_slash(f"claim {tid} --claimer {OWNER}")
    kc.run_slash(f"heartbeat {tid} --claimer {OWNER} --ttl 7200")
    assert _task(tid).claim_expires - int(time.time()) > 7200 - 30


def test_heartbeat_never_shortens_a_claim(kanban_home):
    """A job claimed with a long --ttl must not shrink to the default on its first heartbeat."""
    tid = _ready_task()
    kc.run_slash(f"claim {tid} --claimer {OWNER} --ttl 14400")
    before = _task(tid).claim_expires
    kc.run_slash(f"heartbeat {tid} --claimer {OWNER}")
    assert _task(tid).claim_expires >= before


def test_heartbeat_by_a_non_holder_fails_and_changes_nothing(kanban_home):
    tid = _ready_task()
    kc.run_slash(f"claim {tid} --claimer {OWNER}")
    expires = int(time.time()) + 60
    _set_claim_expires(tid, expires)

    out = kc.run_slash(f"heartbeat {tid} --claimer {OTHER}")
    assert "not held" in out, out
    assert _task(tid).claim_expires == expires
    with kbc.connect() as conn:
        kinds = [e.kind for e in kb.list_events(conn, tid)]
    assert "heartbeat" not in kinds


def test_heartbeat_ttl_without_a_claimer_is_refused(kanban_home):
    """--ttl only means something for a claim the caller can name; silently ignoring it
    would let an anonymous claimer believe the card was extended."""
    tid = _ready_task()
    kc.run_slash(f"claim {tid}")
    expires = _task(tid).claim_expires
    out = kc.run_slash(f"heartbeat {tid} --ttl 7200")
    assert "--claimer" in out, out
    assert _task(tid).claim_expires == expires


def test_worker_scope_guard_runs_before_any_renewal(kanban_home, monkeypatch):
    """A worker scoped to one task must not renew a claim on another, even by naming its holder."""
    tid = _ready_task()
    kc.run_slash(f"claim {tid} --claimer {OWNER}")
    expires = int(time.time()) + 60
    _set_claim_expires(tid, expires)
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_other")
    monkeypatch.setenv("HERMES_KANBAN_RUN_ID", "1")
    out = kc.run_slash(f"heartbeat {tid} --claimer {OWNER}")
    assert "scoped to task t_other" in out, out
    assert _task(tid).claim_expires == expires


def test_renewed_named_claim_survives_the_stale_sweep(kanban_home):
    tid = _ready_task()
    kc.run_slash(f"claim {tid} --claimer {OWNER}")
    _set_claim_expires(tid, int(time.time()) + 60)
    kc.run_slash(f"heartbeat {tid} --claimer {OWNER}")
    with kbc.connect() as conn:
        assert kb.release_stale_claims(conn) == 0
    assert _task(tid).status == "running"


def test_lapsed_named_claim_is_still_reclaimed(kanban_home):
    """Renewal is the holder's job: a named claim nobody heartbeats lapses at its TTL."""
    tid = _ready_task()
    kc.run_slash(f"claim {tid} --claimer {OWNER}")
    _set_claim_expires(tid, int(time.time()) - 1)
    with kbc.connect() as conn:
        assert kb.release_stale_claims(conn) == 1
    task = _task(tid)
    assert task.status == "ready" and task.claim_lock is None


# ---------------------------------------------------------------------------
# complete / request-review: the fence
# ---------------------------------------------------------------------------

def test_named_claim_fences_completion_by_anyone_else(kanban_home):
    tid = _ready_task()
    kc.run_slash(f"claim {tid} --claimer {OWNER}")

    out = kc.run_slash(f"complete {tid} --result 'not mine'")
    assert "cannot complete" in out, out
    out = kc.run_slash(f"complete {tid} --result 'not mine' --claimer {OTHER}")
    assert "cannot complete" in out, out
    assert _task(tid).status == "running"

    out = kc.run_slash(f"complete {tid} --result 'mine' --claimer {OWNER}")
    assert f"Completed {tid}" in out, out
    assert _task(tid).status == "done"


def test_owner_completes_with_the_claimer_from_the_environment(kanban_home, monkeypatch):
    tid = _ready_task()
    kc.run_slash(f"claim {tid} --claimer {OWNER}")
    monkeypatch.setenv("HERMES_KANBAN_CLAIMER", OWNER)
    out = kc.run_slash(f"complete {tid} --result 'mine'")
    assert f"Completed {tid}" in out, out


def test_force_still_overrides_a_named_claim(kanban_home):
    tid = _ready_task()
    kc.run_slash(f"claim {tid} --claimer {OWNER}")
    out = kc.run_slash(f"complete {tid} --result 'operator' --force")
    assert f"Completed {tid}" in out, out


def test_expired_named_claim_no_longer_fences(kanban_home):
    tid = _ready_task()
    kc.run_slash(f"claim {tid} --claimer {OWNER}")
    _set_claim_expires(tid, int(time.time()) - 1)
    out = kc.run_slash(f"complete {tid} --result 'picked up a lapsed card'")
    assert f"Completed {tid}" in out, out


def test_named_claim_fences_request_review(kanban_home):
    tid = _ready_task()
    kc.run_slash(f"claim {tid} --claimer {OWNER}")

    out = kc.run_slash(f"request-review {tid} --summary 'steal'")
    assert "live claim" in out, out
    assert _task(tid).status == "running"

    out = kc.run_slash(f"request-review {tid} --summary 'ready for review' --claimer {OWNER}")
    assert f"Requested review for {tid}" in out, out
    assert _task(tid).status == "review"


def test_library_fence_covers_named_claims(kanban_home):
    """The fence lives in kanban_db, so library callers (tools, plugins) get it too."""
    tid = _ready_task()
    with kbc.connect() as conn:
        claimed = kb.claim_task(conn, tid, claimer=OWNER)
        with pytest.raises(kb.LiveClaimError):
            kb.complete_task(conn, tid, result="not mine")
        assert kb.complete_task(conn, tid, result="mine",
                                expected_run_id=claimed.current_run_id) is True
