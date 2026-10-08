"""Manual acceptance of one existing triage specification; no inference or dispatch."""
from __future__ import annotations

import hashlib
import json

from hermes_cli import kanban_db as kb
from hermes_cli import kanban_db_connect as kbc
from hermes_cli.kanban_output import _err, _json_out


def spec_sha256(row) -> str:
    """Bind review to every saved task field except its lifecycle status."""
    fields = dict(row)
    fields.pop("status", None)
    return hashlib.sha256(json.dumps(fields, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def cmd_accept_spec(args) -> int:
    if not getattr(args, "board", None):
        return _err("accept-spec requires an explicit --board", 2)
    expected = getattr(args, "expected_spec_sha256", None)
    dry_run = bool(getattr(args, "dry_run", False))
    if not dry_run and (not expected or len(expected) != 64 or
                        any(char not in "0123456789abcdef" for char in expected)):
        return _err("accept-spec requires the SHA-256 from a reviewed dry-run", 2)
    try:
        with kbc.connect_closing() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (args.task_id,)).fetchone()
            if row is None or row["status"] != "triage":
                return _err("accept-spec requires an existing triage task")
            if any(row[key] is not None for key in ("claim_lock", "current_run_id", "worker_pid")):
                return _err("accept-spec refuses an active claim or run")
            digest = spec_sha256(row)
            if expected and expected != digest:
                return _err("specification changed; review a fresh dry-run")
            if not dry_run:
                if not kb.specify_triage_task(conn, args.task_id,
                                             expected_spec_sha256=expected, recompute=False):
                    return _err("task moved out of triage before acceptance")
                saved = conn.execute("SELECT * FROM tasks WHERE id = ?", (args.task_id,)).fetchone()
                if saved["status"] != "todo" or spec_sha256(saved) != digest:
                    return _err("acceptance readback differs; inspect the task before dispatch")
    except ValueError as exc:
        return _err(str(exc))
    result = {"task_id": args.task_id, "board": args.board, "spec_sha256": digest,
              "dry_run": dry_run, "status": "triage" if dry_run else "todo"}
    if not _json_out(args, result):
        print(f"{args.task_id}: {result['status']} (spec SHA-256 {digest})")
    return 0
