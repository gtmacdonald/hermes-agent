"""Completion refusals for ``kanban_db.complete_task``: typed errors and run-policy gates.

Every refusal is a ``ValueError`` so tool and dashboard error handlers treat it
as recoverable: the gate runs before any task state changes.
"""
from __future__ import annotations

import json

REVIEWED_POLICY_KEY = "reviewed_worker_policy"


class HallucinatedCardsError(ValueError):
    """``complete_task`` refused: ``created_cards`` has ids that don't exist or
    weren't created by this worker (``.phantom``). A ``ValueError`` so tool
    error handlers treat it as recoverable."""

    def __init__(self, phantom: list[str], completing_task_id: str):
        self.phantom = list(phantom)
        self.completing_task_id = completing_task_id
        super().__init__(
            f"completion blocked: claimed created_cards that do not exist "
            f"or were not created by this worker: {', '.join(phantom)}"
        )


class EmptyCompletionError(ValueError):
    """``complete_task`` refused: no substantive ``result``, ``summary``, or
    stored result. A ``ValueError`` so tool error handlers treat it as
    recoverable. Review approvals are exempt (the human is the record)."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        super().__init__(
            f"completion blocked: {task_id} has no result or summary evidence"
        )


class InspectionOnlyCompletionError(ValueError):
    """A real inspection artifact is not full canonical implementation acceptance."""


def _run_metadata(row) -> dict:
    return json.loads(row["metadata"] or "{}") if row else {}


def gate_inspection_completion(conn, task_id: str) -> None:
    """Refuse completion while the card's latest run was admitted as inspection-only."""
    row = conn.execute("SELECT metadata FROM task_runs WHERE task_id=? ORDER BY id DESC LIMIT 1",
                       (task_id,)).fetchone()
    policy = _run_metadata(row).get(REVIEWED_POLICY_KEY, {})
    if isinstance(policy, dict) and policy.get("completion_mode") == "inspection":
        raise InspectionOnlyCompletionError(
            "Inspection-only run cannot complete the canonical implementation. "
            "Use kanban_request_review or kanban_block with the report and remaining work.")


def carry_reviewed_policy(conn, run_id: int, metadata):
    """Closing metadata never drops or replaces the policy the run was admitted under."""
    stored = conn.execute("SELECT metadata FROM task_runs WHERE id=?", (run_id,)).fetchone()
    prior = _run_metadata(stored)
    if REVIEWED_POLICY_KEY not in prior:
        return metadata
    return {**(metadata or {}), REVIEWED_POLICY_KEY: prior[REVIEWED_POLICY_KEY]}
