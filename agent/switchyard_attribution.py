# Switchyard request attribution (2026-08-17).
#
# Switchyard's routing log (~/.hermes/logs/switchyard-routing.jsonl) records
# ``task`` / ``trial_id`` / ``session_id`` per request — but only when the
# caller sends the three intake headers it reads (switchyard-server 0.2.0
# ``routing_log.rs``: ``RoutingLogContext::from_headers``). Hermes never sent
# them, so every row logged nulls and spend could not be tied back to a
# session or aux job.
#
# This module builds those headers. It is deliberately header-only and
# localhost-gated: attribution is ambient telemetry, so it must never change
# request semantics for a provider that does not know the headers.
#
# Header contract (server side, verified live 2026-08-17):
#   proxy_x_session_id       -> routing-log ``session_id``
#   x-switchyard-intake-task -> routing-log ``task``
#   x-switchyard-trial-id    -> routing-log ``trial_id``
# Judged routes (llm_classifier) carry the headers onto the FINAL target's
# row too — the judge's own call and the picked target both log the same
# attribution, separated by ``tier: "classifier"``.

from __future__ import annotations

import threading
from typing import Any, Dict, Optional
from urllib.parse import urlparse

# Mirror of switchyard-server's header constants (routing_log.rs). Keep in
# sync with the crate if it ever renames them.
SESSION_ID_HEADER = "proxy_x_session_id"
TASK_HEADER = "x-switchyard-intake-task"
TRIAL_ID_HEADER = "x-switchyard-trial-id"

# Loopback hostnames — switchyard binds 0.0.0.0:4000 but hermes always
# reaches it (and any other local router/proxy) via loopback. Headers sent
# to a non-switchyard loopback service are inert unknown headers, so the
# coarse host check is safe and avoids port coupling.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

# Aux calls made outside any agent turn (CLI one-shots, scripts) have no
# session to attribute; the task header alone still identifies the job.
_UNATTRIBUTED_SESSION = "standalone"

_warned = False
_warn_lock = threading.Lock()


def is_switchyard_base_url(base_url: Optional[str]) -> bool:
    """True when *base_url* points at a loopback endpoint (switchyard et al).

    Deliberately coarse: port-agnostic (4000 today, but the router moves —
    aurora card pending) and host-agnostic across loopback spellings. A false
    positive only adds inert headers to another local service.
    """
    if not base_url or not isinstance(base_url, str):
        return False
    try:
        host = (urlparse(base_url).hostname or "").lower()
    except ValueError:
        return False
    return host in _LOOPBACK_HOSTS


def build_attribution_headers(
    base_url: Optional[str],
    *,
    task: Optional[str] = None,
    session_id: Optional[str] = None,
    turn_id: Optional[str] = None,
) -> Dict[str, str]:
    """Build switchyard intake headers for a request to *base_url*.

    Returns ``{}`` for non-loopback targets — nothing to attribute. Values:

    * ``task``        — ``aux:{job}`` for auxiliary calls, ``main`` for the
      agent loop (mirrors relay_llm's ``call_role`` vocabulary so routing-log
      rows and relay traces join cleanly).
    * ``session_id``  — ambient session from the aux accounting context when
      the caller does not supply one (``standalone`` if no context exists,
      so unattributed calls are still countable as a class).
    * ``turn_id``     — trial id; one routing-log ``trial_id`` per agent turn.
    """
    if not is_switchyard_base_url(base_url):
        return {}
    if session_id is None:
        session_id = _ambient_session_id()
    headers: Dict[str, str] = {}
    task_value = str(task or "main").strip()
    if task_value:
        headers[TASK_HEADER] = task_value
    if session_id and str(session_id).strip():
        headers[SESSION_ID_HEADER] = str(session_id).strip()
    if turn_id and str(turn_id).strip():
        headers[TRIAL_ID_HEADER] = str(turn_id).strip()
    return headers


def merge_attribution_headers(
    headers: Optional[Dict[str, str]],
    attribution: Dict[str, str],
) -> Optional[Dict[str, str]]:
    """Merge *attribution* into caller-provided *headers* without clobbering.

    Caller-set values win on collision (an explicit per-request header
    outranks ambient telemetry); attribution keys absent from the caller's
    dict are added. Always returns a fresh dict when there is anything to
    merge, preserving the copy semantics of the previous
    ``dict(extra_headers)`` assignments; returns ``None`` only when both
    inputs are empty so callers keep their fast path.
    """
    if not attribution:
        return dict(headers) if isinstance(headers, dict) else headers
    merged = dict(headers or {})
    for key, value in attribution.items():
        if value and key not in merged:
            merged[key] = value
    return merged


def aux_task_label(task: Optional[str]) -> str:
    """Normalize an aux task name to the ``aux:{job}`` routing-log label."""
    job = str(task or "unknown").strip() or "unknown"
    return job if job.startswith("aux:") else f"aux:{job}"


def _ambient_session_id() -> Optional[str]:
    """Best-effort ambient session from the aux accounting context.

    Never raises: attribution must not break a call. Falls back to
    ``standalone`` outside any agent turn so those rows remain queryable as
    a class rather than logging nulls again.
    """
    global _warned
    try:
        from agent.aux_accounting import get_accounting_context

        ctx = get_accounting_context()
        if ctx is not None:
            session_id = str(ctx[1] or "").strip()
            if session_id:
                return session_id
    except Exception:
        if not _warned:
            with _warn_lock:
                if not _warned:
                    _warned = True
    return _UNATTRIBUTED_SESSION


def apply_to_request_kwargs(
    kwargs: Dict[str, Any],
    *,
    base_url: Optional[str],
    task: Optional[str] = None,
    session_id: Optional[str] = None,
    turn_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Entry point for the aux client: merge attribution into *kwargs* in place.

    ``kwargs`` is the dict passed to ``client.chat.completions.create``;
    attribution lands under ``extra_headers``, merging with — never
    replacing — caller-provided headers. No-op (returns *kwargs* unchanged)
    for non-loopback base URLs.
    """
    attribution = build_attribution_headers(
        base_url,
        task="main" if not task or str(task) == "main" else aux_task_label(task),
        session_id=session_id,
        turn_id=turn_id,
    )
    if not attribution:
        return kwargs
    existing = kwargs.get("extra_headers")
    if isinstance(existing, dict) and existing:
        # Something already set per-request headers (copilot x-initiator,
        # caller-supplied). Merge without clobbering them.
        from agent.switchyard_attribution import merge_attribution_headers

        kwargs["extra_headers"] = merge_attribution_headers(existing, attribution)
    else:
        kwargs["extra_headers"] = attribution
    return kwargs
