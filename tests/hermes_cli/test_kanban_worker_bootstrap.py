"""Fork-local kanban worker isolation: private spec handoff, pinned routes, local-only overlay."""
import os
import stat
from types import SimpleNamespace

import pytest

from hermes_cli import kanban_worker_bootstrap as wb

LOCAL = {"HERMES_KANBAN_TASK": "t_1", "HERMES_KANBAN_LOCAL_PROVIDER": "lan",
         "HERMES_KANBAN_LOCAL_MODEL": "qwen-local", "HERMES_KANBAN_LOCAL_ENDPOINT": "http://192.168.1.20:8080/v1"}


@pytest.fixture
def local_worker(monkeypatch):
    for key, value in LOCAL.items():
        monkeypatch.setenv(key, value)


def test_query_file_carries_the_verbatim_spec_privately(tmp_path):
    task = SimpleNamespace(id="t_1", current_run_id=7, title="Do it", body="Exact spec\nline two")
    path = wb.write_worker_query(task, str(tmp_path / "ws"), "studio", tmp_path / "logs")
    text = path.read_text()
    assert path.parent == (tmp_path / "logs").resolve()
    assert text.endswith("Canonical specification (verbatim):\nExact spec\nline two")
    assert '"task_id": "t_1"' in text and '"run_id": 7' in text
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_workers_drop_the_fallback_chain_but_chat_keeps_it(monkeypatch):
    chain = [{"provider": "openrouter"}]
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    assert wb.worker_fallback_chain(chain) is chain
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_1")
    assert wb.worker_fallback_chain(chain) == []


def test_overlay_is_a_no_op_outside_local_workers(monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "t_1")
    monkeypatch.delenv("HERMES_KANBAN_LOCAL_PROVIDER", raising=False)
    config = {"auxiliary": {"provider": "openrouter"}}
    assert wb.local_worker_overlay(config) is config


def test_overlay_pins_everything_to_the_lan_route_without_mutating_input(local_worker):
    config = {"auxiliary": {"provider": "openrouter", "vision": {"provider": "openrouter", "model": "v"}},
              "fallback_model": [{"provider": "x"}], "mcp_servers": {"s": {}},
              "agent": {"run_budget_seconds": 7200, "max_turns": 30, "disabled_toolsets": "web"}}
    out = wb.local_worker_overlay(config)
    assert config["auxiliary"]["provider"] == "openrouter" and config["mcp_servers"] == {"s": {}}
    assert out["auxiliary"]["provider"] == "lan" and out["auxiliary"]["vision"]["model"] == "qwen-local"
    assert out["delegation"]["base_url"] == LOCAL["HERMES_KANBAN_LOCAL_ENDPOINT"]
    assert out["fallback_model"] == [] and out["fallback_providers"] == [] and out["mcp_servers"] == {}
    assert out["agent"]["run_budget_seconds"] == 1800 and out["agent"]["max_turns"] == 30
    assert {"web", "moa", "delegate", "memory", "hindsight", "vision"} <= set(out["agent"]["disabled_toolsets"])
    assert out["memory"] == {"memory_enabled": False, "user_profile_enabled": False}


def test_aux_route_is_restricted_to_the_reviewed_endpoint(local_worker):
    endpoint = LOCAL["HERMES_KANBAN_LOCAL_ENDPOINT"]
    assert wb.restrict_auxiliary_route("auto", None, None) == ("lan", "qwen-local", endpoint)
    assert wb.restrict_auxiliary_route("lan", "m", endpoint + "/") == ("lan", "qwen-local", endpoint)
    with pytest.raises(wb.KanbanWorkerToolPolicyError):
        wb.restrict_auxiliary_route("openrouter", None, None)
    with pytest.raises(wb.KanbanWorkerToolPolicyError):
        wb.restrict_auxiliary_route("lan", None, "https://openrouter.ai/api/v1")


def test_aux_route_normalizes_provider_spelling(local_worker):
    endpoint = LOCAL["HERMES_KANBAN_LOCAL_ENDPOINT"]
    for provider in (None, " LAN ", "Auto"):
        assert wb.restrict_auxiliary_route(provider, None, None) == ("lan", "qwen-local", endpoint)


@pytest.mark.parametrize("missing", ["HERMES_KANBAN_LOCAL_MODEL", "HERMES_KANBAN_LOCAL_ENDPOINT"])
def test_partial_local_pin_fails_closed_with_a_policy_error(local_worker, monkeypatch, missing):
    monkeypatch.delenv(missing)
    with pytest.raises(wb.KanbanWorkerToolPolicyError, match="missing"):
        wb.local_worker_overlay({})
    with pytest.raises(wb.KanbanWorkerToolPolicyError, match="missing"):
        wb.restrict_auxiliary_route("auto", None, None)
