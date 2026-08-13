#!/usr/bin/env python3
"""switchyard-evaluate.py — probe what's live, regenerate USE-CASE routes.

Supersedes ensure-switchyard.py's route authoring. Design (Greg, 2026-08-13):
no model-alias passthroughs — every route is a hermes use case, and a CAPABLE
model does the arbitration (a wrong route = a failed, confused session):

    hermes-main        llm_classifier — judge (haiku/luna) picks weak vs strong
    hermes-titles      first live cheap/fast model
    hermes-compaction  first live long-context cheap model

Edit MODELS and USE_CASES to taste, rerun. Deterministic and idempotent:
probes each catalog model with a 1-token call (keys from ~/.hermes/.env),
picks the first LIVE entry of each preference list, hash-guards the write,
validates with `switchyard-server --dry-run`, restarts the service, and
refreshes the `switchyard` provider block in every hermes profile config
that already has one.

Usage:
  switchyard-evaluate.py            # probe → write → validate → restart → sync provider blocks
  switchyard-evaluate.py --dry-run  # probe + print the plan, change nothing
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

HOME = Path.home()
ENV_FILE = HOME / ".hermes" / ".env"
ROUTES = HOME / ".hermes" / "switchyard" / "routes.toml"
BIN = HOME / ".cargo" / "bin" / "switchyard-server"
SERVICE = "com.hermes.switchyard"
PROFILE_CONFIGS = [HOME / ".hermes" / "config.yaml"] + sorted(
    (HOME / ".hermes" / "profiles").glob("*/config.yaml")
)

CLIENTS = {
    "openrouter": {"format": "openai_chat", "base_url": "https://openrouter.ai/api/v1", "key": "OPENROUTER_API_KEY"},
    "kimi":       {"format": "openai_chat", "base_url": "https://api.kimi.com/coding/v1", "key": "KIMI_API_KEY"},
    "anthropic":  {"format": "anthropic_messages", "base_url": "https://api.anthropic.com", "key": "ANTHROPIC_API_KEY"},
    "openai":     {"format": "openai_chat", "base_url": "https://api.openai.com/v1", "key": "OPENAI_API_KEY"},
    "meta":       {"format": "openai_chat", "base_url": "https://api.meta.ai/v1", "key": "META_API_KEY"},
    "local":      {"format": "openai_chat", "base_url": "http://127.0.0.1:8081/v1", "key": None},
}

# The model catalog. `extra` lands in the target's extra_body.
MODELS = {
    "kimi":     {"client": "kimi", "id": "kimi-k3", "extra": {"temperature": 1.0}},
    "deepseek": {"client": "openrouter", "id": "deepseek/deepseek-v4-pro-0813"},
    "spark":    {"client": "meta", "id": "muse-spark-1.2"},
    "haiku":    {"client": "anthropic", "id": "claude-haiku-4-5-20251001"},
    "luna":     {"client": "openai", "id": "gpt-4o"},
    "glimmer":  {"client": "local", "id": "unsloth/muse-glimmer-30b"},
    "qwen4b":   {"client": "local", "id": "qwen/qwen3-4b-2507"},  # Instruct-2507: never thinks, snappy, free
}

# One route per hermes use case. Preference lists: first LIVE model wins.
# kind=classifier → llm_classifier (judge/weak/strong); kind=fixed → single target.
USE_CASES = {
    "hermes-main": {
        "kind": "classifier",
        "judge": ["haiku", "luna"],          # capable arbitration until deepseek is vetted
        "weak": ["kimi", "qwen4b"],
        "strong": ["deepseek", "spark"],
        "threshold": 0.5,
        "affinity": True,
    },
    "hermes-titles":     {"kind": "fixed", "prefer": ["qwen4b", "kimi"]},  # local-first (Greg, 2026-08-13)
    "hermes-compaction": {"kind": "fixed", "prefer": ["kimi", "deepseek"]},
    # Effort tiers — names match hermes /reasoning levels 1:1 (Greg, 2026-08-13).
    # Serving models must TOLERATE reasoning params (agent.reasoning_overrides
    # pins one per tier): kimi and deepseek verified OK; haiku REJECTS every
    # effort value via switchyard's anthropic translation ("adaptive thinking
    # is not supported") — haiku is judge-only, never a serving target.
    # Depth within the kimi band comes from the override, not the model.
    **{f"hermes-{lvl}": {"kind": "fixed", "prefer": prefs} for lvl, prefs in {
        "none":    ["kimi", "qwen4b"],
        "minimal": ["kimi", "qwen4b"],
        "low":     ["kimi", "qwen4b"],
        "medium":  ["kimi", "deepseek"],
        "high":    ["deepseek", "spark"],
        "xhigh":   ["deepseek", "spark"],
        "max":     ["deepseek", "spark"],
        "ultra":   ["deepseek", "spark"],
    }.items()},
}

PROBE_TIMEOUT = 25


def load_env() -> dict:
    env = {}
    for line in ENV_FILE.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env


def probe(name: str, env: dict) -> tuple[bool, str]:
    """1-token live call. Returns (alive, detail)."""
    m = MODELS[name]
    c = CLIENTS[m["client"]]
    key = env.get(c["key"]) if c["key"] else None
    if c["key"] and not key:
        return False, f"no {c['key']} in .env"
    try:
        if c["format"] == "anthropic_messages":
            req = urllib.request.Request(
                c["base_url"] + "/v1/messages",
                data=json.dumps({"model": m["id"], "max_tokens": 1,
                                 "messages": [{"role": "user", "content": "ping"}]}).encode(),
                headers={"Content-Type": "application/json", "x-api-key": key,
                         "anthropic-version": "2023-06-01"})
        else:
            headers = {"Content-Type": "application/json"}
            if key:
                headers["Authorization"] = f"Bearer {key}"
            req = urllib.request.Request(
                c["base_url"] + "/chat/completions",
                data=json.dumps({"model": m["id"], "max_tokens": 1,
                                 "messages": [{"role": "user", "content": "ping"}]}).encode(),
                headers=headers)
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT):
            return True, "live"
    except Exception as e:
        return False, str(e)[:100]


def first_live(prefs: list[str], live: dict) -> str | None:
    return next((p for p in prefs if live.get(p)), None)


def build_routes(live: dict) -> str:
    used: set[str] = set()
    routes_toml, errors = [], []
    for route, spec in USE_CASES.items():
        if spec["kind"] == "classifier":
            picks = {slot: first_live(spec[slot], live) for slot in ("judge", "weak", "strong")}
            missing = [s for s, p in picks.items() if p is None]
            if missing:
                errors.append(f"{route}: no live model for {missing} (prefs exhausted)")
                continue
            used.update(picks.values())
            routes_toml.append(
                f'[routes.{route}]\nid = "{route}"\ntype = "llm_classifier"\nmode = "capability"\n'
                f'classifier_target = "{picks["judge"]}"\nweak_target = "{picks["weak"]}"\n'
                f'strong_target = "{picks["strong"]}"\nbase_threshold = {spec["threshold"]}\n'
                f'session_affinity = {str(spec["affinity"]).lower()}\n')
        else:
            pick = first_live(spec["prefer"], live)
            if pick is None:
                errors.append(f"{route}: no live model in {spec['prefer']}")
                continue
            used.add(pick)
            # ponytail: TOML type is passthrough — but the route NAME is the use
            # case and the target is evaluator-chosen; there are no alias routes.
            routes_toml.append(
                f'[routes.{route}]\nid = "{route}"\ntype = "passthrough"\ntarget = "{pick}"\n')
    if errors:
        sys.exit("✗ cannot build routes:\n  " + "\n  ".join(errors))

    parts = ["# Switchyard routes — USE-CASE routing, one route per hermes job.",
             "# Managed by scripts/switchyard-evaluate.py (fork: ~/src/hermes). Rerun to re-evaluate.",
             "# No model-alias passthroughs by design (Greg, 2026-08-13).", "",
             "schema_version = 1", ""]
    for cname in sorted({MODELS[u]["client"] for u in used}):
        c = CLIENTS[cname]
        parts.append(f'[llm_clients.{cname}]\nformat = "{c["format"]}"\nbase_url = "{c["base_url"]}"')
        if c["key"]:
            parts.append(f'api_key_env = "{c["key"]}"')
        parts.append("max_retries = 2\n")
    for u in sorted(used):
        m = MODELS[u]
        parts.append(f'[targets.{u}]\nid = "{m["id"]}"\nllm_client = "{m["client"]}"')
        if m.get("extra"):
            body = ", ".join(f"{k} = {v}" for k, v in m["extra"].items())
            parts.append(f"extra_body = {{ {body} }}")
        parts.append("")
    parts.extend(routes_toml)
    return "\n".join(parts)


PROVIDER_BLOCK = """  switchyard:
    name: Switchyard (use-case router)
    base_url: http://127.0.0.1:4000/v1
    model: hermes-main
    discover_models: false
    models:
{models}
    default_model: hermes-main
"""


def sync_provider_block(cfg_path: Path, dry: bool) -> str:
    text = cfg_path.read_text()
    pat = re.compile(r"^  switchyard:\n(?:^(?:    .*|)\n)*", re.M)
    m = pat.search(text)
    if not m:
        return "no switchyard provider — skipped"
    models = "\n".join(f"      {r}: {{}}" for r in USE_CASES)
    new = PROVIDER_BLOCK.format(models=models)
    if m.group(0) == new:
        return "provider block current"
    if not dry:
        cfg_path.write_text(text[:m.start()] + new + text[m.end():])
    return "provider block updated"


def main() -> None:
    dry = "--dry-run" in sys.argv
    env = load_env()
    print("── probing catalog ──")
    live = {}
    for name in MODELS:
        ok, detail = probe(name, env)
        live[name] = ok
        print(f"  {'✓' if ok else '✗'} {name:10} {MODELS[name]['id']:40} {detail}")
    content = build_routes(live)
    print("\n── plan ──")
    for line in content.splitlines():
        if line.startswith("[routes.") or line.startswith(("classifier_target", "weak_target", "strong_target", "target")):
            print("  " + line)
    if dry:
        print("\n[dry-run] no files written")
        return
    if hashlib.sha256(content.encode()).hexdigest() == hashlib.sha256(ROUTES.read_bytes()).hexdigest():
        print("\nroutes.toml unchanged")
    else:
        tmp = ROUTES.with_suffix(".toml.new")
        tmp.write_text(content)
        # validator resolves api_key_env, so hand it the .env values
        v = subprocess.run([str(BIN), "--config", str(tmp), "--dry-run"],
                           capture_output=True, text=True, env={**os.environ, **env})
        if v.returncode != 0:
            sys.exit(f"✗ switchyard validation failed:\n{v.stderr.strip()}")
        tmp.replace(ROUTES)
        print(f"\n✓ routes.toml written + validated")
    uid = subprocess.run(["id", "-u"], capture_output=True, text=True).stdout.strip()
    subprocess.run(["launchctl", "kickstart", "-k", f"gui/{uid}/{SERVICE}"], check=True)
    print(f"✓ {SERVICE} restarted")
    for cfg in PROFILE_CONFIGS:
        label = cfg.parent.name if cfg.parent.name != ".hermes" else "default"
        print(f"  {label:12} {sync_provider_block(cfg, dry)}")
    print("✓ done — hermes routes:", ", ".join(USE_CASES))


if __name__ == "__main__":
    main()
