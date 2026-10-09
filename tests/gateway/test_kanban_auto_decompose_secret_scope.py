"""Auto-decompose tick under multiplex.

Regression for #107955 / #57837: the tick runs off-turn in a fresh Context, so
``get_secret`` fails closed unless the tick installs the launch profile's scope.
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

from gateway import kanban_watchers_dispatcher as kwd
from gateway.kanban_watchers_common import _to_thread_process_service


def _dispatcher(home):
    settings = kwd._DispatcherSettings(60.0, None, None, 2, 0, True, None, None)
    kb = SimpleNamespace(DEFAULT_BOARD="default", kanban_home=lambda: home)
    return kwd._KanbanDispatcher(kb, settings)


def test_auto_decompose_tick_is_disabled_under_reviewed_dispatch(monkeypatch, tmp_path):
    """Fork-local: reviewed-scope dispatch never authorizes generated cards, so the tick
    decomposes nothing and never reaches the decomposer (upstream's #107955 multiplex
    secret-scope regression has no path to run here)."""
    monkeypatch.setattr(kwd, "_board_slugs", lambda kb: ["default"])
    calls = []
    fake = SimpleNamespace(list_triage_ids=lambda: calls.append("list") or ["t1"],
                           decompose_task=lambda *a, **k: calls.append("decompose"))
    monkeypatch.setitem(sys.modules, "hermes_cli.kanban_decompose", fake)

    decomposed = asyncio.run(_to_thread_process_service(_dispatcher(tmp_path).auto_decompose_tick, 5))

    assert decomposed == 0
    assert calls == []
