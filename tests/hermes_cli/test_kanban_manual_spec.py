"""Manual specification approval preserves routing and never touches other cards."""
import argparse
import json
from pathlib import Path

import pytest

from hermes_cli import kanban as cli
from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli.kanban_manual_spec import spec_sha256


@pytest.fixture
def board(tmp_path, monkeypatch):
    home = tmp_path / 'hermes'
    home.mkdir()
    monkeypatch.setenv('HERMES_HOME', str(home))
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    kb.create_board('approval')
    return 'approval'


def run(*args):
    parser = argparse.ArgumentParser()
    cli.build_parser(parser.add_subparsers(dest='cmd'))
    return cli.kanban_command(parser.parse_args(['kanban', *args]))


def row(conn, task_id):
    return dict(conn.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone())


def test_accept_preserves_all_fields_other_cards_and_dependencies(board, capsys):
    with kbc.connect_closing(board=board) as conn:
        parent = kb.create_task(conn, title='unfinished parent')
        child = kb.create_task(conn, title='Original exact title', body='Do not activate.\nLocal ONLY.',
                               triage=True, assignee='quick', parents=[parent],
                               model_override='local-model', provider_override='local-provider')
        other = kb.create_task(conn, title='unrelated triage', triage=True)
        unrelated_todo = kb.create_task(conn, title='unrelated todo', parents=[parent])
        before = row(conn, child)
        untouched = [row(conn, x) for x in (parent, other, unrelated_todo)]
        links = list(conn.execute('SELECT * FROM task_links'))
        count = conn.execute('SELECT count(*) FROM task_events').fetchone()[0]
    assert run('--board', board, 'accept-spec', child, '--dry-run', '--json') == 0
    digest = json.loads(capsys.readouterr().out)['spec_sha256']
    with kbc.connect_closing(board=board) as conn:
        assert conn.execute('SELECT count(*) FROM task_events').fetchone()[0] == count
    assert run('--board', board, 'accept-spec', child, '--expected-spec-sha256', digest) == 0
    with kbc.connect_closing(board=board) as conn:
        after = row(conn, child)
        assert after.pop('status') == 'todo'
        before.pop('status')
        assert after == before
        assert [row(conn, x) for x in (parent, other, unrelated_todo)] == untouched
        assert list(conn.execute('SELECT * FROM task_links')) == links
        assert conn.execute("SELECT kind FROM task_events WHERE task_id=? ORDER BY id DESC LIMIT 1", (child,)).fetchone()[0] == 'specified'


def test_parent_free_stays_todo_without_board_wide_promotion(board):
    with kbc.connect_closing(board=board) as conn:
        task = kb.create_task(conn, title='fully specified', triage=True)
        digest = spec_sha256(row(conn, task))
    assert run('--board', board, 'accept-spec', task, '--expected-spec-sha256', digest) == 0
    with kbc.connect_closing(board=board) as conn:
        assert kb.get_task(conn, task).status == 'todo'


@pytest.mark.parametrize('case', ['missing_board', 'missing_hash', 'wrong_hash', 'wrong_state'])
def test_invalid_approval_leaves_state_unchanged(board, case):
    with kbc.connect_closing(board=board) as conn:
        task = kb.create_task(conn, title='review me', triage=case != 'wrong_state')
        before = row(conn, task)
        digest = spec_sha256(before)
    argv = ['--board', board, 'accept-spec', task, '--expected-spec-sha256', digest]
    if case == 'missing_board': argv = argv[2:]
    if case == 'missing_hash': argv = argv[:-2]
    if case == 'wrong_hash': argv[-1] = '0' * 64
    assert run(*argv) != 0
    with kbc.connect_closing(board=board) as conn:
        assert row(conn, task) == before


def test_guard_rejects_changed_spec_and_rewrite(board):
    with kbc.connect_closing(board=board) as conn:
        task = kb.create_task(conn, title='review me', body='Original conditions', triage=True)
        digest = spec_sha256(row(conn, task))
        kb.edit_task(conn, task, body='Changed conditions')
        with pytest.raises(ValueError, match='changed'):
            kb.specify_triage_task(conn, task, expected_spec_sha256=digest, recompute=False)
        digest = spec_sha256(row(conn, task))
        with pytest.raises(ValueError, match='rewrite'):
            kb.specify_triage_task(conn, task, body='replacement', expected_spec_sha256=digest, recompute=False)
        assert kb.get_task(conn, task).status == 'triage'


def test_active_claim_refused(board):
    with kbc.connect_closing(board=board) as conn:
        task = kb.create_task(conn, title='claimed triage fixture', triage=True)
        # Model an inconsistent/stale claim; production code must fail closed.
        conn.execute('UPDATE tasks SET claim_lock=? WHERE id=?', ('other-owner', task))
        before = row(conn, task)
    assert run('--board', board, 'accept-spec', task, '--dry-run') != 0
    with kbc.connect_closing(board=board) as conn:
        assert row(conn, task) == before


def test_accept_does_not_touch_a_different_board_or_call_auxiliary(board, monkeypatch):
    from hermes_cli import kanban_specify
    def refuse(*args, **kwargs):
        raise AssertionError('manual acceptance must not invoke an LLM')
    monkeypatch.setattr(kanban_specify, '_call_aux', refuse)
    kb.create_board('other')
    with kbc.connect_closing(board='other') as conn:
        other = kb.create_task(conn, title='other board task', triage=True)
        before = row(conn, other)
    with kbc.connect_closing(board=board) as conn:
        task = kb.create_task(conn, title='selected board task', triage=True)
        digest = spec_sha256(row(conn, task))
    assert run('--board', board, 'accept-spec', task, '--expected-spec-sha256', digest) == 0
    with kbc.connect_closing(board='other') as conn:
        assert row(conn, other) == before
