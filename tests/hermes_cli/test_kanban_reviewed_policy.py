"""Reviewed worker policy: absent policy keeps today's behaviour; inspection refusals stay recoverable."""
import argparse
import json
import types
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli.kanban_dispatch_scope import reviewed_worker_args
from tests.hermes_cli.test_kanban_reviewed_dispatch import _approved_card


@pytest.fixture
def board(tmp_path, monkeypatch):
    home = tmp_path / "hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.create_board("reviewed")
    return "reviewed"


def test_policy_less_admission_spawns_unbounded_and_completes_normally(board, monkeypatch):
    from hermes_cli import kanban_db_dispatch as kbd
    monkeypatch.setattr(kbd, "_profile_exists_fn", lambda: lambda profile: True)
    monkeypatch.setattr(kb, "_fire_worker_spawned_hook", lambda *a, **k: None)
    with kbc.connect_closing(board=board) as conn:
        task_id, entry = _approved_card(conn)
        commands = []
        def spawn(task, workspace, board):
            commands.append(kbd._worker_argv(task, "studio", None, query_file="/tmp/spec"))
        assert len(kbd.dispatch_once(conn, board=board, eligibility_scope=[entry], spawn_fn=spawn).spawned) == 1
        assert "--max-turns" not in commands[0] and "--run-budget" not in commands[0]
        run_id = conn.execute("SELECT current_run_id FROM tasks WHERE id=?", (task_id,)).fetchone()[0]
        stored = conn.execute("SELECT metadata FROM task_runs WHERE id=?", (run_id,)).fetchone()[0]
        assert "reviewed_worker_policy" not in json.loads(stored or "{}")
        assert not conn.execute("SELECT 1 FROM task_events WHERE task_id=? AND kind='reviewed_worker_policy'",
                                (task_id,)).fetchone()
        assert kb.complete_task(conn, task_id, expected_run_id=run_id, result="Done",
                                metadata={"note": "kept"})
        closed = json.loads(conn.execute("SELECT metadata FROM task_runs WHERE id=?", (run_id,)).fetchone()[0])
        assert closed == {"note": "kept"}


def test_task_without_policy_adds_no_native_limits():
    assert reviewed_worker_args(types.SimpleNamespace(id="t_plain")) == []


def test_cli_complete_reports_inspection_refusal_without_traceback(monkeypatch, capsys):
    from hermes_cli import kanban as cli

    @contextmanager
    def fake_connect_closing():
        yield MagicMock()

    def refuse(conn, tid, **kw):
        # The class the CLI catches: other tests may have re-imported the kanban modules.
        raise cli.kb.InspectionOnlyCompletionError("Inspection-only run cannot complete the canonical implementation.")

    monkeypatch.setattr("hermes_cli.kanban.kb.get_task",
                        lambda conn, tid: types.SimpleNamespace(id=tid, goal_mode=False, title="t", body="b"))
    monkeypatch.setattr("hermes_cli.kanban.kb.complete_task", refuse)
    monkeypatch.setattr("hermes_cli.kanban.kbc.connect_closing", fake_connect_closing)
    monkeypatch.setattr("hermes_cli.kanban._worker_run_id_for", lambda _: None)
    args = argparse.Namespace(task_ids=["t_insp"], summary="report", result=None, metadata=None)
    assert cli._cmd_complete(args) == 1
    err = capsys.readouterr().err
    assert "cannot complete t_insp: Inspection-only run" in err and "request-review" in err
