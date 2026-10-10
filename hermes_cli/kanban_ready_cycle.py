"""Bounded local selection for finite reviewed READY work, inside the existing gateway.

No hosted judging, broad promotion, retries or credentials changes. Durable attempts
survive gateway restarts. The dispatch claim still rechecks the resulting receipt.
"""
from __future__ import annotations
import hashlib
import ipaddress
import json
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit
from hermes_cli.kanban_dispatch_scope import (FIELDS, HARNESS_BOARDS, SCOPED_ASSIGNEES, task_digest,
                                              reviewed_entries)


def spec_digest(row):
    fields = [k for k in FIELDS if k not in {"model_override", "provider_override"}]
    return hashlib.sha256(json.dumps({k: row[k] for k in fields}, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def approvals(config, now=None):
    now = time.time() if now is None else now
    result = []
    scope = config.get("dispatch_ready_scope", [])
    if not isinstance(scope, list):
        return result
    for e in scope:
        if not isinstance(e, dict) or e.get("approved") is not True:
            continue
        if e.get("board") in HARNESS_BOARDS or not isinstance(e.get("board"), str):
            continue
        if type(e.get("expires_at")) not in (int, float) or e["expires_at"] <= now:
            continue
        if not all(isinstance(e.get(k), str) and e[k].strip() for k in
                   ("task_id", "spec_sha256", "model", "provider", "endpoint")):
            continue
        # Deliberately direct private LAN routes only: no switchyard or hosted proxy.
        try:
            u = urlsplit(e["endpoint"])
            address = ipaddress.ip_address(u.hostname)
            if u.scheme != "http" or not address.is_private or (address.is_loopback and (str(address) != "127.0.0.1" or u.port != 8081)) or u.username or u.password:
                continue
        except ValueError:
            continue
        if "muse" in (e["model"] + e["provider"]).lower():
            continue
        result.append(e)
    return result


def candidate(conn, entry):
    row = conn.execute("SELECT * FROM tasks WHERE id=?", (entry["task_id"],)).fetchone()
    if row is None or row["status"] != "ready" or row["claim_lock"] is not None:
        return None
    if row["assignee"] not in SCOPED_ASSIGNEES or spec_digest(row) != entry["spec_sha256"]:
        return None
    if conn.execute("SELECT 1 FROM task_runs WHERE task_id=? LIMIT 1", (entry["task_id"],)).fetchone():
        return None
    if conn.execute("SELECT 1 FROM task_links l LEFT JOIN tasks p ON p.id=l.parent_id WHERE l.child_id=? "
                    "AND (p.id IS NULL OR p.status NOT IN ('done','archived')) LIMIT 1", (entry["task_id"],)).fetchone():
        return None
    return row


class ReadyCycle:
    def __init__(self, kb, run=None, clock=time.time):
        self.kb = kb
        self.run = run or subprocess.run
        self.clock = clock
        self.root = Path(kb.kanban_home()) / "kanban" / "ready-cycle-receipts"

    def _path(self, e):
        # Changing a model choice must not erase a failed attempt for the same work.
        key = hashlib.sha256((e["board"] + "\0" + e["task_id"] + "\0" + e["spec_sha256"]).encode()).hexdigest()
        return self.root / (key + ".json")

    def pending(self, e):
        """No receipt yet: the next cycle may still select this approval."""
        return not self._path(e).exists()

    def _read(self, path):
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data, indent=2) + "\n")
        temp.replace(path)

    def _route_matches(self, row, e):
        # Verify the profile route still has the reviewed LAN endpoint, before assigning.
        import hermes_yaml as yaml
        try:
            cfg = yaml.safe_load((Path(self.kb.kanban_home()) / "profiles" / row["assignee"] / "config.yaml").read_text())
            route = cfg.get("providers", {}).get(e["provider"], {})
            return route.get("base_url", "").rstrip("/") == e["endpoint"].rstrip("/") and (
                e["model"] in route.get("models", {}) or e["model"] == route.get("model"))
        except Exception:
            return False

    def scopes(self, config):
        result = []
        for e in approvals(config, self.clock()):
            r = self._read(self._path(e))
            scope = r.get("scope", {})
            if r.get("state") == "selected" and r.get("model") == e["model"] and r.get("provider") == e["provider"]:
                # Live expiry is authoritative; receipts cannot extend authorization.
                scope = dict(scope, expires_at=min(scope.get("expires_at", 0), e["expires_at"]))
                result.extend(reviewed_entries([scope], e["board"]))
        return result

    def tick(self, config):
        entries = approvals(config, self.clock())
        if not entries:
            return
        interval = config.get("dispatch_ready_interval_seconds", 300)
        budget = config.get("dispatch_ready_max_per_cycle", 1)
        if type(interval) not in (int, float) or type(budget) is not int:
            return
        interval, budget = max(300, interval), min(3, max(0, budget))
        marker = self.root / "cycle.json"
        last = self._read(marker).get("at", 0)
        if type(last) not in (int, float) or self.clock() - last < interval:
            return
        # Existing gateway singleton serializes ticks. Persist first so restart cannot burst.
        self._save(marker, {"at": self.clock()})
        from hermes_cli import kanban_db_connect as kbc
        count = 0
        for e in entries:
            if count >= budget:
                break
            path = self._path(e)
            if not self.pending(e):
                continue  # selected, failed, interrupted all require a fresh human review
            conn = kbc.connect(board=e["board"])
            try:
                row = candidate(conn, e)
                if row is None:
                    continue
                receipt = {"at": self.clock(), "board": e["board"], "task_id": e["task_id"],
                           "spec_sha256": e["spec_sha256"], "model": e["model"], "provider": e["provider"],
                           "selection": "Greg-authorized manual local override; dual judges skipped", "state": "selecting"}
                self._save(path, receipt)
                count += 1
                if not self._route_matches(row, e):
                    self._save(path, dict(receipt, state="held", reason="profile LAN route changed"))
                    continue
                try:
                    # Manual override never invokes scrub, gateway_key or either hosted judge.
                    r = self.run([str(Path.home()/".local/bin/card-model"), e["task_id"], "--board", e["board"],
                                  "--profile", row["assignee"], "--model", e["model"], "--provider", e["provider"],
                                  "--apply", "--json"], capture_output=True, text=True, timeout=120)
                    receipt["exit_code"] = r.returncode
                    row = candidate(conn, e)
                    if r.returncode != 0 or row is None or row["model_override"] != e["model"] or row["provider_override"] != e["provider"]:
                        self._save(path, dict(receipt, state="held", reason="selection failed or task changed"))
                        continue
                    bodies = conn.execute("SELECT body FROM task_comments WHERE task_id=? ORDER BY id DESC", (e["task_id"],)).fetchall()
                    judge = next((c["body"] for c in bodies if c["body"].startswith("card-model (D-265):")
                                  and e["model"] in c["body"] and e["provider"] in c["body"]), None)
                    if not judge:
                        self._save(path, dict(receipt, state="held", reason="selection evidence missing"))
                        continue
                    scope = dict(board=e["board"], task_id=e["task_id"], approved=True, expires_at=e["expires_at"],
                                 local_endpoint=e["endpoint"], local_provider=e["provider"], local_model=e["model"],
                                 task_sha256=task_digest(row), judge_sha256=hashlib.sha256(judge.encode()).hexdigest())
                    self._save(path, dict(receipt, state="selected", scope=scope))
                except (OSError, subprocess.TimeoutExpired):
                    self._save(path, dict(receipt, state="held", reason="selection command unavailable or timed out"))
            finally:
                conn.close()
