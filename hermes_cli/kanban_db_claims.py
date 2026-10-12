"""Claim identity and liveness: who holds a ``running`` card, and whether that claim still protects it.

A claim lock is host-local (``host:pid``: a dispatcher worker, or an anonymous CLI
claim, on this host) or named (``<kind>:<id>``: an external harness session such as
``claude:<session>`` or ``codex:<thread>``, the holder form those harnesses already use
for leases). The two kinds are judged differently: a worker by its process, a named
claim by its lease (``claim_expires``), which its holder renews with
``heartbeat_claim``. The identity is cooperative: it prevents collisions between
harnesses, not impersonation.

Split out of ``hermes_cli.kanban_db``; origin-resident helpers are reached
late-bound via ``_kb`` (import-cycle breaking) so monkeypatching
``kanban_db.<name>`` keeps working.
"""

from __future__ import annotations

import os
import re
import time
from typing import Optional

_NAMED_CLAIMER_RE = re.compile(r"^[a-z0-9-]+:[A-Za-z0-9._:=-]+$")


def claimer_problem(claimer: str) -> Optional[str]:
    """Why ``claimer`` cannot name an external claim, or None. A named claim must not
    look like a host-local worker lock, or it would be judged by a pid that is not its process."""
    if not _NAMED_CLAIMER_RE.match(claimer or ""):
        return (f"claimer {claimer!r} must be <kind>:<id> (kind: lowercase letters, digits, -), "
                "e.g. claude:<session-id>")
    prefix = _kb._host_prefix()
    host = prefix[:-1]
    host_names = {host.lower(), host.split(".", 1)[0].lower()}
    kind, ident = claimer.split(":", 1)
    if claimer.startswith(prefix) or kind in host_names or ident.lower() in host_names:
        return (f"claimer {claimer!r} names this host, not a harness session; "
                "use <kind>:<id>, e.g. claude:<session-id>")
    return None


def is_named_claim(lock: Optional[str]) -> bool:
    """True for a claim held by an external ``<kind>:<id>`` claimer rather than a worker on this host."""
    return bool(lock and _NAMED_CLAIMER_RE.match(lock) and not lock.startswith(_kb._host_prefix()))


def _claim_is_live(trow) -> bool:
    """True when a ``running`` task's claim still protects a run.

    A worker-backed claim is live while the worker process it spawned exists
    (PID + start-time fingerprint); TTL expiry is deliberately not consulted,
    because ``release_stale_claims`` extends, not reclaims, the claim of a live
    worker. A host-local claim whose worker is gone, or that never spawned one,
    has no run to protect. A named claim has no process to check, so it is live
    until ``claim_expires``."""
    if trow["status"] != "running" or trow["claim_lock"] is None:
        return False
    if trow["worker_pid"]:
        return _kb._worker_alive(trow["worker_pid"], trow["worker_started_at"])
    expires = _kb._row_get(trow, "claim_expires")
    return is_named_claim(trow["claim_lock"]) and expires is not None and int(expires) > time.time()


class LiveClaimError(ValueError):
    """``complete_task`` refused: the task is ``running`` under a live claim and
    the caller neither owns its run (``expected_run_id``) nor passed ``force``.
    Completing anyway would close the worker's run row underneath a process
    that is still executing. ``holder`` is the ``claim_lock`` that refused, so a
    refusal can name a named claim's holder. A ``ValueError`` so tool error
    handlers treat it as recoverable."""

    def __init__(self, task_id: str, holder: Optional[str] = None):
        self.holder = holder
        if is_named_claim(holder):
            message = (
                f"{task_id} is claimed by {holder} until its lease expires; complete it as "
                f"that holder (claimer={holder}) or with force=True and a reason (explicit "
                "operator override)"
            )
        else:
            message = (
                f"{task_id} is running under a live worker claim; pass expected_run_id "
                "(worker ownership) or force=True (explicit operator override) instead "
                "of closing the live run"
            )
        super().__init__(message)


# --- Actor: who did it ---------------------------------------------------------
# Lifecycle events name who acted. The actor lives in the JSON payload (no schema
# change); an emitter that already knows it (``created_by``, ``--claimer``) sets it first.
_ACTOR_EVENT_KINDS = frozenset({"claimed", "heartbeat", "completed", "reclaimed", "blocked"})


def resolve_actor(explicit: Optional[str] = None) -> str:
    """Who is acting on the board: an explicit claimer (``--claimer``), then a dispatched
    worker's own lock (``$HERMES_KANBAN_CLAIM_LOCK``), then an external harness's
    ``$HERMES_KANBAN_CLAIMER``, then this process's ``host:pid`` -- the claim fence's order."""
    # Per-session identity, not configuration (same class as kanban._named_claimer).
    worker_lock = os.environ.get("HERMES_KANBAN_CLAIM_LOCK")  # health: allow HX002 -- per-session holder identity
    env_claimer = os.environ.get("HERMES_KANBAN_CLAIMER")  # health: allow HX002 -- per-session holder identity
    return explicit or worker_lock or env_claimer or _kb._claimer_id()


def _with_actor(kind: str, payload: Optional[dict]) -> Optional[dict]:
    """``payload`` with an ``actor`` for the lifecycle kinds in :data:`_ACTOR_EVENT_KINDS`,
    unless it already names one; other kinds pass through unchanged."""
    if kind not in _ACTOR_EVENT_KINDS or (payload and payload.get("actor")):
        return payload
    return {**(payload or {}), "actor": resolve_actor()}


def _completed_actor_payload(
    payload: dict, *, actor: Optional[str], force: bool, force_reason: Optional[str],
) -> dict:
    """The ``completed`` event names its actor; a forced completion also records
    ``forced`` and the operator's ``forced_reason`` (None when ``--force`` came bare)."""
    payload = {**payload, "actor": resolve_actor(actor)}
    if force:
        payload.update(forced=True, forced_reason=force_reason)
    return payload


def _fence_live_claim(trow, task_id: str, *, expected_run_id: Optional[int], force: bool) -> None:
    """The one fence for every verb that clears a running card's claim (complete, block,
    schedule): raise :class:`LiveClaimError` naming the holder when the claim is live and the
    caller neither owns its run (``expected_run_id``) nor asked for an operator override (``force``)."""
    if expected_run_id is None and not force and trow is not None and _claim_is_live(trow):
        raise LiveClaimError(task_id, holder=trow["claim_lock"])


# Late-bound origin namespace (see module docstring); imported LAST so this
# module is fully populated before ``kanban_db`` imports from it.
from hermes_cli import kanban_db as _kb
