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


# Late-bound origin namespace (see module docstring); imported LAST so this
# module is fully populated before ``kanban_db`` imports from it.
from hermes_cli import kanban_db as _kb
