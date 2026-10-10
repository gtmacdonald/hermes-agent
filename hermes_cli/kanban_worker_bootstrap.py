"""Private full-spec handoff for the normal dispatcher worker; no tool grants."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile


class KanbanWorkerToolPolicyError(ValueError):
    """An owned worker cannot execute its required lifecycle with its tool policy."""


def write_worker_query(task, workspace: str, board: str, directory: Path) -> Path:
    """Keep the accepted spec out of process argv and outside the worker workspace.

    Retain successful handoffs beside existing worker logs for run diagnostics.
    A caller may remove its newly created handoff if process creation fails.
    """
    directory.mkdir(parents=True, exist_ok=True)
    metadata = json.dumps({"board": board, "task_id": task.id, "run_id": task.current_run_id,
                           "title": task.title, "workspace": str(Path(workspace).resolve())},
                          ensure_ascii=False)
    query = (
        "You own the assigned Kanban task described below. The full canonical specification "
        "is supplied here; do not spend iterations trying to recover it from the task ID.\n"
        + metadata + "\n"
        "Use only your actual tool schemas; this handoff grants no additional tools. "
        "Preserve every constraint and approval gate in the specification. If required work "
        "cannot be performed with the available tools, block the task with the concrete reason; "
        "do not fabricate results or use an unapproved route.\n"
        "Resolve artifact paths explicitly under the absolute workspace above unless the "
        "accepted specification authorizes another destination. Finish through the existing "
        "Kanban completion/review protocol, or block with evidence.\n"
        "Canonical specification (verbatim):\n" + (task.body or "")
    )
    fd, name = tempfile.mkstemp(prefix="worker-query-", suffix=".txt", dir=directory.resolve())
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(query)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return Path(name)


def worker_fallback_chain(chain):
    """Workers keep their explicitly selected route; chat retains its configured chain."""
    return [] if os.environ.get("HERMES_KANBAN_TASK", "").strip() else chain


def _local_route():
    """The dispatcher-pinned LAN route of a local-only owned worker, else None.

    A partial pin fails closed: a local-only worker must never fall back to hosted routes."""
    if not os.environ.get("HERMES_KANBAN_TASK") or not os.environ.get("HERMES_KANBAN_LOCAL_PROVIDER"):
        return None
    model = os.environ.get("HERMES_KANBAN_LOCAL_MODEL", "").strip()
    endpoint = os.environ.get("HERMES_KANBAN_LOCAL_ENDPOINT", "").strip()
    if not model or not endpoint:
        raise KanbanWorkerToolPolicyError(
            "local-only worker is missing HERMES_KANBAN_LOCAL_MODEL or HERMES_KANBAN_LOCAL_ENDPOINT")
    return {"provider": os.environ["HERMES_KANBAN_LOCAL_PROVIDER"].strip(), "model": model, "endpoint": endpoint}


def local_worker_overlay(config):
    """Ephemeral local-only route for owned workers; never alters cached/on-disk config."""
    local = _local_route()
    if local is None:
        return config
    import copy
    config = copy.deepcopy(config)
    route = dict(provider=local["provider"], model=local["model"], base_url=local["endpoint"],
                 api_mode="chat_completions", fallback_chain=[])
    auxiliary = config.setdefault("auxiliary", {})
    auxiliary.update(route)
    for key, value in list(auxiliary.items()):
        if isinstance(value, dict):
            auxiliary[key] = dict(value, **route)
    config["fallback_model"] = []
    config["fallback_providers"] = []
    agent = config.setdefault("agent", {})
    agent["service_tier"] = "normal"
    old_budget = agent.get("run_budget_seconds")
    agent["run_budget_seconds"] = min(old_budget, 1800) if type(old_budget) in (int, float) and old_budget > 0 else 1800
    old_turns = agent.get("max_turns")
    agent["max_turns"] = min(old_turns, 80) if type(old_turns) is int and old_turns > 0 else 80
    config.setdefault("delegation", {}).update(route)
    config.setdefault("memory", {}).update(memory_enabled=False, user_profile_enabled=False)
    # These optional tools use independent remote model backends or memory stores.
    disabled = config["agent"].get("disabled_toolsets", [])
    disabled = disabled.split(",") if isinstance(disabled, str) else list(disabled or [])
    config["agent"]["disabled_toolsets"] = list(set(disabled) | {"moa", "delegate", "memory", "hindsight", "vision"})
    config["mcp_servers"] = {}
    return config


def restrict_auxiliary_route(provider, model, base_url):
    """Fail before opening any hosted client in a local-only worker."""
    local = _local_route()
    if local is None:
        return provider, model, base_url
    requested = (provider or "").strip().lower()
    endpoint = local["endpoint"]
    if requested not in {local["provider"].lower(), "auto", ""} or (
            base_url and base_url.strip().rstrip("/") != endpoint.rstrip("/")):
        raise KanbanWorkerToolPolicyError("local-only worker refused an auxiliary route outside its reviewed LAN endpoint")
    return local["provider"], local["model"], endpoint
