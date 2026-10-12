"""F-039: the reviewed-scope dispatcher says when it can start nothing.

Ready cards assigned to a dispatch profile, with every approval expired, used to sit for
days with no log line: ``ready_nonempty`` counts only reviewed work, so the "dispatcher
stuck" health warning never fires. The dispatcher now warns once per change instead.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from gateway import kanban_watchers_dispatcher as kwd
from hermes_cli import kanban_dispatch_scope as scope


def _settings():
    return kwd._DispatcherSettings(60.0, None, None, 2, 0, True, None, None)


def _warnings_and_infos(caplog):
    records = [r for r in caplog.records if "admits" in r.getMessage()]
    return ([r.getMessage() for r in records if r.levelno == logging.WARNING],
            [r.getMessage() for r in records if r.levelno == logging.INFO])


def test_starvation_is_logged_once_per_change(monkeypatch, tmp_path, caplog):
    states = iter([
        ({"acc": 4, "cosc3301": 1}, set()),
        ({"acc": 4, "cosc3301": 1}, set()),   # unchanged: silent
        ({"acc": 5}, {"cosc3301"}),           # unreadable board keeps its last state
        RuntimeError("config unreadable"),    # unknown: keep state, stay silent
        ({}, set()),                          # cleared: one info line
        ({}, set()),
        ({"acc": 1}, set()),                  # starved again: warn again
    ])

    def fake(kb, kanban_cfg, *, ready_cycle=None):
        state = next(states)
        if isinstance(state, Exception):
            raise state
        return state

    monkeypatch.setattr(scope, "starved_boards_readonly", fake)
    monkeypatch.setattr("hermes_cli.config.load_config", lambda: {"kanban": {}})
    kb = SimpleNamespace(DEFAULT_BOARD="default", kanban_home=lambda: tmp_path)
    dispatcher = kwd._KanbanDispatcher(kb, _settings())
    with caplog.at_level(logging.INFO, logger="gateway.run"):
        for _ in range(7):
            dispatcher.report_starvation()
    warnings, infos = _warnings_and_infos(caplog)
    assert len(warnings) == 2 and len(infos) == 1
    assert "acc=4" in warnings[0] and "cosc3301=1" in warnings[0]
    assert "acc=1" in warnings[1] and "cosc3301" not in warnings[1]


@pytest.fixture
def home(tmp_path, monkeypatch):
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    return hermes_home


def test_expired_approvals_starve_the_dispatcher_and_it_says_so(home, caplog):
    """The F-039 shape: a professor card is ready on ``acc`` and nothing admits it."""
    from hermes_cli import kanban_db as kb
    from hermes_cli import kanban_db_connect as kbc

    kb.create_board("acc")
    with kbc.connect_closing(board="acc") as conn:
        task_id = kb.create_task(conn, title="Proposal", body="Spec", assignee="professor")
        conn.execute("UPDATE tasks SET status='ready' WHERE id=?", (task_id,))
    dispatcher = kwd._KanbanDispatcher(kb, _settings())
    # Reviewed-only telemetry sees nothing, so the generic "stuck" warning cannot fire.
    assert dispatcher.ready_nonempty() is False
    with caplog.at_level(logging.INFO, logger="gateway.run"):
        dispatcher.tick_once()
        dispatcher.tick_once()
    warnings, _infos = _warnings_and_infos(caplog)
    assert len(warnings) == 1 and "acc=1" in warnings[0]
