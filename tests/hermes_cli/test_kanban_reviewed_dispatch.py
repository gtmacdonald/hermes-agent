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


def test_reviewed_tick_reclaims_running_workers_without_promoting(board, monkeypatch):
    from hermes_cli import kanban_db_dispatch as kbd
    calls = []
    real = kbd._run_reclaim_phase
    monkeypatch.setattr(kbd, "_run_reclaim_phase",
                        lambda *a, **k: calls.append(k) or real(*a, **k))
    with kbc.connect_closing(board=board) as conn:
        todo = kb.create_task(conn, title="Parent-free, unreviewed")
        conn.execute("UPDATE tasks SET status='todo' WHERE id=?", (todo,))
        kbd.dispatch_once(conn, board=board, eligibility_scope=[], spawn_fn=lambda *a, **k: None)
        assert conn.execute("SELECT status FROM tasks WHERE id=?", (todo,)).fetchone()[0] == "todo"
    assert len(calls) == 1 and calls[0]["promote"] is False


def test_boards_with_running_work_are_found_read_only(board):
    from hermes_cli.kanban_dispatch_scope import boards_with_running_readonly
    kb.create_board("idle")
    with kbc.connect_closing(board=board) as conn:
        task_id = kb.create_task(conn, title="In flight")
        conn.execute("UPDATE tasks SET status='running' WHERE id=?", (task_id,))
    assert boards_with_running_readonly(kb) == {board}


# --- F-039: say when the reviewed scope admits none of the waiting work ---

def _ready_card(conn, **overrides):
    fields = dict(title="Waiting work", body="Spec", assignee="professor")
    fields.update(overrides)
    task_id = kb.create_task(conn, **fields)
    conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (task_id,))
    return task_id


def test_admission_finds_waiting_cards_no_live_approval_admits(board):
    from hermes_cli.kanban_dispatch_scope import admission
    with kbc.connect_closing(board=board) as conn:
        task_id, entry = _approved_card(conn)
        _ready_card(conn, assignee="quick")  # scoped dispatch never starts other profiles
        _ready_card(conn, assignee=None)
        claimed = _ready_card(conn)
        conn.execute("UPDATE tasks SET claim_lock='held' WHERE id=?", (claimed,))
        assert admission(conn, board, {}, kb) == ([task_id], [])
        expired = dict(entry, expires_at=time.time() - 1)
        assert admission(conn, board, {"dispatch_scope": [expired]}, kb) == ([task_id], [])
        assert admission(conn, board, {"dispatch_scope": [entry]}, kb) == ([task_id], [entry])


def test_admission_ignores_harness_boards(board):
    from hermes_cli.kanban_dispatch_scope import admission
    kb.create_board("claude")
    with kbc.connect_closing(board="claude") as conn:
        _ready_card(conn)
        assert admission(conn, "claude", {}, kb) == ([], [])


def test_admission_honours_dispatch_profiles(board, tmp_path):
    from hermes_cli.kanban_dispatch_scope import admission
    (tmp_path / "hermes" / "config.yaml").write_text(
        "kanban:\n  dispatch_profiles: [professor]\n", encoding="utf-8")
    with kbc.connect_closing(board=board) as conn:
        professor = _ready_card(conn)
        _ready_card(conn, assignee="studio")
        assert admission(conn, board, {}, kb) == ([professor], [])


def test_admission_counts_ready_cycle_approvals_until_a_receipt_holds_them(board):
    from hermes_cli.kanban_dispatch_scope import admission
    from hermes_cli.kanban_ready_cycle import ReadyCycle, spec_digest
    with kbc.connect_closing(board=board) as conn:
        task_id = _ready_card(conn)
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        config = _ready(task_id=task_id, spec_sha256=spec_digest(row))
        approval = config["dispatch_ready_scope"][0]
        assert admission(conn, board, config, kb) == ([task_id], [approval])
        cycle = ReadyCycle(kb)
        cycle._save(cycle._path(approval), {"state": "held"})
        assert admission(conn, board, config, kb) == ([task_id], [])


def test_starved_boards_are_found_read_only(board):
    from hermes_cli.kanban_dispatch_scope import starved_boards_readonly
    for slug in ("approved", "claude", "broken"):
        kb.create_board(slug)
    with kbc.connect_closing(board=board) as conn:
        _ready_card(conn)
        _ready_card(conn)
    with kbc.connect_closing(board="approved") as conn:
        _task_id, entry = _approved_card(conn)
    with kbc.connect_closing(board="claude") as conn:
        _ready_card(conn)
    kb.kanban_db_path(board="broken").write_bytes(b"not a database" * 64)
    scope = {"dispatch_scope": [dict(entry, board="approved")]}
    assert starved_boards_readonly(kb, scope) == ({board: 2}, {"broken"})


def _diagnostics(json_out, severity=None):
    import argparse
    from hermes_cli import kanban as kanban_cli
    return kanban_cli._cmd_diagnostics(argparse.Namespace(task=None, severity=severity, json=json_out))


def test_diagnostics_cli_reports_a_starved_board(board, monkeypatch, capsys):
    import json
    monkeypatch.setenv("HERMES_KANBAN_BOARD", board)
    with kbc.connect_closing(board=board) as conn:
        _ready_card(conn)
    assert _diagnostics(True) == 0
    row = json.loads(capsys.readouterr().out)[-1]
    assert row["task_id"] is None
    assert [(d["kind"], d["severity"]) for d in row["diagnostics"]] == [("dispatch_scope_starved", "error")]
    assert row["diagnostics"][0]["data"]["waiting"] == 1
    assert _diagnostics(False) == 0
    text = capsys.readouterr().out
    assert "dispatch_scope_starved" in text
    assert "No active diagnostics" not in text


def test_diagnostics_cli_warns_before_an_approval_expires(board, monkeypatch, capsys, tmp_path):
    import json
    monkeypatch.setenv("HERMES_KANBAN_BOARD", board)
    with kbc.connect_closing(board=board) as conn:
        _task_id, entry = _approved_card(conn)
    (tmp_path / "hermes" / "config.yaml").write_text(
        json.dumps({"kanban": {"dispatch_scope": [entry]}}), encoding="utf-8")
    assert _diagnostics(True) == 0
    row = json.loads(capsys.readouterr().out)[-1]
    assert [(d["kind"], d["severity"]) for d in row["diagnostics"]] == [("approval_expiring", "warning")]
    assert _diagnostics(True, severity="error") == 0
    assert json.loads(capsys.readouterr().out)[-1]["diagnostics"] == []
