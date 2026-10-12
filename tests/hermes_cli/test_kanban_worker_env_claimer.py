"""A dispatched worker holds its card as its own claim lock, never as an external harness.

``HERMES_KANBAN_CLAIMER`` names the external session (``claude:<session>``, ...) that a
human-driven ``hermes kanban`` command speaks for. A gateway started from such a session
would otherwise hand that name to every worker it spawns, and the worker's claims,
comment authors and event actors would read as the harness instead of the worker.
"""

import os

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_dispatch as kbd


def _spawn_env(monkeypatch, tmp_path) -> dict:
    captured = {}

    class _Proc:
        pid = 4321

    def _fake_popen(cmd, **kwargs):
        captured["env"] = kwargs["env"]
        return _Proc()

    monkeypatch.setattr("subprocess.Popen", _fake_popen)
    monkeypatch.setattr(kbd, "_retag_legacy_worker_sessions", lambda _root: None)
    monkeypatch.setattr(kb, "worker_logs_dir", lambda board=None: tmp_path / "logs")
    task = kb.Task(
        id="t_c1a1e0e0", title="work", body=None, assignee="default", status="running",
        priority=0, created_by=None, created_at=0, started_at=None, completed_at=None,
        workspace_kind="scratch", workspace_path=None, claim_lock="host:4242",
        claim_expires=None, tenant=None,
    )
    workspace = str(tmp_path / "ws")
    os.makedirs(workspace, exist_ok=True)
    kbd._default_spawn(task, workspace)
    return captured["env"]


def test_worker_env_drops_an_inherited_harness_claimer(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HERMES_KANBAN_CLAIMER", "claude:905f3fd6")

    env = _spawn_env(monkeypatch, tmp_path)

    assert "HERMES_KANBAN_CLAIMER" not in env
    assert env["HERMES_KANBAN_CLAIM_LOCK"] == "host:4242"
