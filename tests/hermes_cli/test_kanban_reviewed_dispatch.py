"""Fork-local reviewed dispatch: only approved, unexpired, hash-bound cards are eligible."""
import hashlib
import time
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli.kanban_dispatch_scope import eligible, reviewed_entries, task_digest
from hermes_cli.kanban_ready_cycle import approvals

JUDGE = "card-model (D-265): tier=standard model=gpt-5.6-sol provider=openai-codex"


@pytest.fixture
def board(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.create_board("reviewed")
    return "reviewed"


def _entry(board="reviewed", **overrides):
    entry = {"board": board, "approved": True, "expires_at": time.time() + 3600,
             "task_id": "t_x", "task_sha256": "a" * 64, "judge_sha256": "b" * 64}
    entry.update(overrides)
    return entry


def test_reviewed_entries_fail_closed():
    good = _entry()
    scope = [good, _entry(approved="yes"), _entry(expires_at=time.time() - 1), _entry(expires_at="later"),
             _entry(task_sha256=""), _entry(board="other"), "not-a-dict"]
    assert reviewed_entries(scope, "reviewed") == [good]
    assert reviewed_entries({"board": "reviewed"}, "reviewed") == []
    # Harness boards are worked by their own sessions, never auto-dispatched.
    assert reviewed_entries([_entry(board="claude")], "claude") == []


def _approved_card(conn, **overrides):
    fields = dict(title="Reviewed work", body="Exact spec", assignee="studio",
                  model_override="gpt-5.6-sol", provider_override="openai-codex")
    fields.update(overrides)
    task_id = kb.create_task(conn, **fields)
    conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (task_id,))
    kb.add_comment(conn, task_id, "card-model", JUDGE)
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
    return task_id, _entry(task_id=task_id, task_sha256=task_digest(row),
                           judge_sha256=hashlib.sha256(JUDGE.encode()).hexdigest())


def test_eligible_requires_the_reviewed_spec_and_judge_verdict(board):
    with kbc.connect_closing(board=board) as conn:
        task_id, entry = _approved_card(conn)
        assert eligible(conn, entry)
        assert not eligible(conn, dict(entry, judge_sha256="c" * 64))
        conn.execute("UPDATE tasks SET body='Edited after review' WHERE id=?", (task_id,))
        assert not eligible(conn, entry)


@pytest.mark.parametrize("overrides", [
    {"assignee": "quick"},
    {"model_override": None, "provider_override": None},
    {"model_override": "muse-spark-1.3-contributor", "provider_override": "meta"},
])
def test_eligible_refuses_unreviewable_routes(board, overrides):
    with kbc.connect_closing(board=board) as conn:
        _task_id, entry = _approved_card(conn, **overrides)
        assert not eligible(conn, entry)


def _ready(**overrides):
    entry = {"board": "reviewed", "approved": True, "expires_at": time.time() + 3600, "task_id": "t_x",
             "spec_sha256": "a" * 64, "model": "qwen-local", "provider": "lan",
             "endpoint": "http://192.168.1.20:8080/v1"}
    entry.update(overrides)
    return {"dispatch_ready_scope": [entry]}


@pytest.mark.parametrize("overrides, admitted", [
    ({}, True),
    ({"endpoint": "http://127.0.0.1:8081/v1"}, True),
    ({"endpoint": "http://127.0.0.1:9000/v1"}, False),
    ({"endpoint": "https://192.168.1.20:8080/v1"}, False),
    ({"endpoint": "http://8.8.8.8:8080/v1"}, False),
    ({"endpoint": "http://lan-box.local:8080/v1"}, False),
    ({"endpoint": "http://user:pw@192.168.1.20:8080/v1"}, False),
    ({"model": "muse-spark-1.3-contributor"}, False),
    ({"board": "codex"}, False),
    ({"approved": 1}, False),
])
def test_ready_cycle_admits_only_direct_private_lan_routes(overrides, admitted):
    assert bool(approvals(_ready(**overrides))) is admitted
