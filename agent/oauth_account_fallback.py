"""Explicit Professor Anthropic account references; no profile or route migration.

References resolve only pool-owned OAuth grants. Identity is captured from the
provider's successful OAuth exchange, bound to the token generation in the
owner store, and never inferred from labels, paths or token shape.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import replace

from hermes_constants import get_default_hermes_root, get_hermes_home, profile_name_for_home

SOURCE = "profile_oauth_ref"
QUOTA_REASONS = frozenset({"quota_exhausted", "usage_limit_reached", "subscription_limit_reached", "insufficient_quota"})


def identity_from_exchange(result: dict) -> dict | None:
    """Called only at the successful HTTPS OAuth exchange boundary."""
    account = result.get("account")
    organization = result.get("organization")
    account_id = account.get("uuid") if isinstance(account, dict) else None
    organization_id = organization.get("uuid") if isinstance(organization, dict) else None
    if not isinstance(account_id, str) or not account_id or not isinstance(organization_id, str) or not organization_id:
        return None
    return {"account_id": account_id, "organization_id": organization_id,
            "source": "oauth_token_response", "binding": _binding(result.get("access_token"))}


def _binding(token: str) -> str:
    # Owner-only generation binding. Never copied into the consumer reference.
    return hashlib.sha256(str(token or "").encode()).hexdigest()


def identity(row: dict) -> tuple[str, str] | None:
    value = row.get("oauth_account_identity")
    if (not isinstance(value, dict) or value.get("source") not in {"oauth_token_response", "oauth_profile_response"}
            or not value.get("account_id") or not value.get("organization_id")
            or not row.get("access_token") or value.get("binding") != _binding(row["access_token"])):
        return None
    return value["account_id"], value["organization_id"]


def enabled(provider: str) -> bool:
    from hermes_cli import auth
    if provider != "anthropic" or profile_name_for_home(get_hermes_home()) != "professor":
        return False
    policy = auth._load_auth_store().get("oauth_account_fallback")
    return isinstance(policy, dict) and policy.get("enabled") is True and policy.get("version") == 1


def _owner_path(owner: str):
    from hermes_constants import named_profile_is_live
    root = get_default_hermes_root().resolve()
    if owner == "studio":
        path = root / "profiles" / "studio"
        if not named_profile_is_live(path):
            raise ValueError("OAuth reference owner unavailable")
    elif owner == "personal":
        path = root
    else:
        raise ValueError("OAuth reference owner is not admitted")
    # Reject symlink escape from the internally resolved registry, never accept a caller path.
    if path.resolve() != path.absolute():
        raise ValueError("OAuth reference owner path must be canonical")
    return path / "auth.json"


def _account_row(row: dict) -> bool:
    return row.get("auth_type") == "oauth" and str(row.get("source", "")).startswith("manual:")


def _owned(row: dict) -> bool:
    # Singletons/Claude Code have another token authority; never refresh a pool
    # shadow of them here. Independent auth-add PKCE rows own their grant.
    from agent.anthropic_credentials import _is_oauth_token
    return (_account_row(row)
            and _is_oauth_token(row.get("access_token", ""))
            and bool(row.get("access_token")) and bool(row.get("refresh_token")))


def _read_owner(entry):
    from hermes_cli import auth
    path = _owner_path(entry.extra.get("owner_profile"))
    store = auth._load_auth_store(path)
    rows = store.get("credential_pool", {}).get("anthropic", [])
    matches = [r for r in rows if r.get("id") == entry.extra.get("owner_credential_id")]
    if len(matches) != 1 or not _owned(matches[0]):
        raise ValueError("OAuth owner grant removed or unsupported")
    row = matches[0]
    ident = identity(row)
    expected = (entry.extra.get("owner_account_id"), entry.extra.get("owner_organization_id"))
    if ident is None or ident != expected:
        raise ValueError("OAuth owner identity changed or unverified")
    return path, store, row


def _quota(row: dict, now: float) -> bool:
    evidence = row.get("account_quota")
    try:
        return (isinstance(evidence, dict) and identity(row) is not None
                and evidence.get("account_id") == identity(row)[0]
                and evidence.get("organization_id") == identity(row)[1]
                and evidence.get("source") == "provider_response"
                and evidence.get("reason") in QUOTA_REASONS
                and row.get("last_status") == "exhausted"
                and row.get("last_error_reason") == evidence.get("reason")
                and 0 <= now - float(evidence["observed_at"]) <= 3600
                and float(evidence["reset_at"]) > now
                and float(row.get("status_cleared_at") or 0) <= float(evidence["observed_at"]))
    except (TypeError, ValueError, KeyError):
        return False


def candidates(entries, model: str | None):
    """Partition before hydration/refresh so a healthy primary never touches a donor."""
    if not entries or not model:
        return [e for e in entries if _owned(e.to_dict())]
    from hermes_cli import auth
    rows = auth._load_auth_store().get("credential_pool", {}).get("anthropic", [])
    by_id = {row.get("id"): row for row in rows}
    # Read the consumer inventory at every admission. Peer removal, reauthentication,
    # newly added accounts, and auth reset must override a stale in-memory pool.
    entries = [e for e in entries if e.id in by_id]
    own = [e for e in entries if _owned(e.to_dict())]
    inventory = [row for row in rows if _account_row(row)]
    policy = auth._load_auth_store().get("oauth_account_fallback", {})
    directed = policy.get("mode") == "user_override" and policy.get("requested_owner") in {"studio", "personal"} and bool(policy.get("authorization_reference"))
    if directed:
        # A deliberate operator action, never an inferred error/quota verdict.
        result = []
        for entry in entries:
            if entry.source != SOURCE or entry.extra.get("owner_profile") != policy.get("requested_owner") or model not in entry.extra.get("verified_models", []):
                continue
            try:
                _, _, row = _read_owner(entry)
                if row.get("last_status") == "dead" or _quota(row, time.time()):
                    continue
                if row.get("last_status") == "exhausted" and float(row.get("last_error_reset_at") or float("inf")) > time.time():
                    continue
                result.append(entry)
            except (ValueError, OSError, TypeError):
                continue
        return result
    # Unknown accounts block fallback. All OAuth rows count, including same-account
    # aliases, and all must have authoritative identity before admission.
    if not own or not inventory or any(identity(row) is None for row in inventory):
        return own
    now = time.time()
    accounts = {identity(row)[0] for row in inventory}
    if not all(any(identity(row)[0] == account and _quota(row, now) for row in inventory) for account in accounts):
        return own
    for owner in ("studio", "personal"):
        admitted = []
        for entry in entries:
            if entry.source != SOURCE or entry.extra.get("owner_profile") != owner:
                continue
            if model not in entry.extra.get("verified_models", []):
                continue
            try:
                _, _, row = _read_owner(entry)
                if identity(row)[0] in accounts or row.get("last_status") == "dead" or _quota(row, now):
                    continue
                # Other owner failure classes remain blocked until an explicit reset/relogin.
                if row.get("last_status") == "exhausted" and float(row.get("last_error_reset_at") or float("inf")) > now:
                    continue
                admitted.append(entry)
            except (ValueError, OSError, TypeError):
                continue
        if admitted:
            return admitted
    return []


def hydrate(entry, *, force: bool = False, model: str | None = None):
    """Owner lock covers re-read -> refresh -> durable commit, before use."""
    from hermes_cli import auth
    from agent import anthropic_credentials as ac
    if not enabled("anthropic"):
        raise ValueError("Professor OAuth account fallback is disabled")
    path = _owner_path(entry.extra.get("owner_profile"))
    with auth._auth_store_lock(), auth._auth_store_lock(target_path=path, timeout_seconds=30):
        consumer_rows = auth._load_auth_store().get("credential_pool", {}).get("anthropic", [])
        consumer = next((r for r in consumer_rows if r.get("id") == entry.id and r.get("source") == SOURCE), None)
        model = model or entry.extra.get("selected_model")
        if consumer is None or not model:
            raise ValueError("OAuth reference removed or model unspecified")
        fields = ("owner_profile", "owner_credential_id", "owner_account_id", "owner_organization_id", "verified_models")
        if any(consumer.get(key) != entry.extra.get(key) for key in fields):
            raise ValueError("OAuth reference changed during selection")
        from agent.credential_pool import PooledCredential
        fresh_entries = [PooledCredential.from_dict("anthropic", r) for r in consumer_rows]
        if entry.id not in {e.id for e in candidates(fresh_entries, model)}:
            raise ValueError("OAuth account quota admission no longer holds")
        _, store, row = _read_owner(entry)
        now = time.time()
        if row.get("last_status") == "dead" or _quota(row, now):
            raise ValueError("OAuth owner grant unavailable")
        if ac.is_rotation_consumed_uncommitted(row["refresh_token"], source_path=path):
            raise ValueError("OAuth owner rotation requires reauthentication")
        needs_refresh = float(row.get("expires_at_ms") or 0) <= (now + 120) * 1000
        # Force means retry a rejected pair, not rotate a newer peer's pair.
        if needs_refresh or (force and entry.access_token == row["access_token"]):
            old_access, old_refresh = row["access_token"], row["refresh_token"]
            try:
                rotated = ac.refresh_anthropic_oauth_pure(old_refresh, use_json=str(row["source"]).endswith("hermes_pkce"))
            except Exception:
                raise ValueError("OAuth owner refresh failed; reauthentication may be required") from None
            ident = dict(row["oauth_account_identity"])
            ident["binding"] = _binding(rotated["access_token"])
            row.update(rotated, oauth_account_identity=ident)
            try:
                auth._save_auth_store(store, target_path=path)
            except Exception:
                # The single-use pair was spent: quarantine on the durable existing
                # sidecar, never expose the uncommitted pair or replay the old one.
                ac.mark_rotation_consumed_uncommitted(old_refresh, old_access, source_path=path)
                raise ValueError("OAuth owner commit failed; reauthentication required") from None
        return replace(entry, access_token=row["access_token"], refresh_token=None,
                       expires_at_ms=row.get("expires_at_ms"), extra={**entry.extra, "selected_model": model})


def observe(pool, entry, status_code, context):
    """Only structured account allowance errors from the actual failed request qualify."""
    from agent.credential_pool import _parse_absolute_timestamp
    from hermes_cli import auth
    if status_code not in (402, 429) or not isinstance(context, dict):
        return False
    reason = context.get("account_quota_reason")
    reset = _parse_absolute_timestamp(context.get("account_quota_reset_at"))
    if reason not in QUOTA_REASONS or not reset or reset <= time.time():
        return False
    now = time.time()
    if entry.source == SOURCE:
        path = _owner_path(entry.extra.get("owner_profile"))
        with auth._auth_store_lock(target_path=path):
            _, store, row = _read_owner(entry)
            if row["access_token"] != entry.access_token:
                return False  # response for a stale generation cannot bench its replacement
            ident = identity(row)
            evidence = {"account_id": ident[0], "organization_id": ident[1], "source": "provider_response",
                        "reason": reason, "observed_at": now, "reset_at": reset}
            for sibling in store["credential_pool"]["anthropic"]:
                if identity(sibling) is not None and identity(sibling)[0] == ident[0]:
                    sibling.update(account_quota={**evidence, "organization_id": identity(sibling)[1]}, last_status="exhausted", last_status_at=now,
                                   last_error_reset_at=reset, last_error_reason=reason)
            auth._save_auth_store(store, target_path=path)
        return True
    ident = identity(entry.to_dict())
    if ident is None:
        return False
    evidence = {"account_id": ident[0], "organization_id": ident[1], "source": "provider_response",
                "reason": reason, "observed_at": now, "reset_at": reset}
    for sibling in list(pool.entries()):
        sibling_identity = identity(sibling.to_dict())
        if sibling_identity is not None and sibling_identity[0] == ident[0]:
            sibling_evidence = {**evidence, "organization_id": sibling_identity[1]}
            pool._replace_entry(sibling, replace(sibling, extra={**sibling.extra, "account_quota": sibling_evidence}))
            pool._mark_exhausted(pool._find(lambda e: e.id == sibling.id), status_code,
                                 {"reason": reason, "reset_at": reset}, persist=False, failure_reason=reason)
    pool._persist()
    return True


def add_reference(*, owner: str, credential_id: str, models: list[str]):
    """Explicit reversible enrollment, only after owner identity/model verification.

    No files are copied and no login, model request or policy activation occurs.
    Models are the caller's explicit previously verified current model inventory.
    """
    import uuid
    from agent.credential_pool import PooledCredential, load_pool
    if profile_name_for_home(get_hermes_home()) != "professor" or not models or not all(isinstance(m, str) and m for m in models):
        raise ValueError("OAuth reference requires Professor and explicit verified models")
    from hermes_cli import auth
    path = _owner_path(owner)
    with auth._auth_store_lock(target_path=path):
        store = auth._load_auth_store(path)
        row = next((r for r in store.get("credential_pool", {}).get("anthropic", []) if r.get("id") == credential_id), None)
        if row is None or not _owned(row) or identity(row) is None:
            raise ValueError("OAuth owner requires verified pool-owned identity")
        ident = identity(row)
    entry = PooledCredential(provider="anthropic", id=uuid.uuid4().hex[:6], label=f"{owner} OAuth reference",
        auth_type="oauth", priority=0, source=SOURCE, access_token="", extra={
            "owner_profile": owner, "owner_credential_id": credential_id, "owner_account_id": ident[0],
            "owner_organization_id": ident[1], "verified_models": list(models)})
    return load_pool("anthropic").add_entry(entry)


class AccountVerificationError(ValueError):
    """Redacted verifier failure; status/code only, never response bodies."""
    def __init__(self, code: str, status: int | None = None):
        self.code, self.status = code, status
        super().__init__("OAuth account verification failed: " + code + (f" (HTTP {status})" if status else ""))


def verify_pool_account(credential_id: str, *, persist: bool = True):
    """Explicit identity enrollment for an EXISTING pool-owned grant.

    One read-only authenticated provider profile request; never a model call,
    login, token refresh, credential copy or profile switch. Errors contain no
    tokens, provider bodies, identity values or token fingerprints.
    """
    import json
    import urllib.request
    import urllib.error
    from hermes_cli import auth
    path = auth._auth_file_path()
    with auth._auth_store_lock(target_path=path):
        store = auth._load_auth_store(path)
        row = next((r for r in store.get("credential_pool", {}).get("anthropic", [])
                    if r.get("id") == credential_id), None)
        if row is None or not _owned(row):
            raise ValueError("Identity enrollment requires an existing pool-owned OAuth grant")
        pair = row["access_token"], row["refresh_token"]
    req = urllib.request.Request("https://api.anthropic.com/api/oauth/profile", headers={
        "Authorization": "Bearer " + pair[0], "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "claude-code/2.1.81", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            payload = json.loads(response.read(65537))
        if not isinstance(payload, dict):
            raise ValueError()
        ident = identity_from_exchange({**payload, "access_token": pair[0]})
        if ident is None:
            raise AccountVerificationError("unsupported_identity_shape")
        ident["source"] = "oauth_profile_response"
    except urllib.error.HTTPError as error:
        raise AccountVerificationError("endpoint_refused", error.code) from None
    except AccountVerificationError:
        raise
    except Exception:
        raise AccountVerificationError("endpoint_unavailable_or_invalid_json") from None
    with auth._auth_store_lock(target_path=path):
        store = auth._load_auth_store(path)
        row = next((r for r in store.get("credential_pool", {}).get("anthropic", [])
                    if r.get("id") == credential_id), None)
        if row is None or (row.get("access_token"), row.get("refresh_token")) != pair:
            raise ValueError("OAuth grant changed during account verification; retry enrollment")
        if not persist:
            # Proof metadata remains private to the caller. No token-generation
            # fingerprint or credential is returned and no enrollment is written.
            return {key: ident[key] for key in ("account_id", "organization_id", "source")}
        row["oauth_account_identity"] = ident
        auth._save_auth_store(store, target_path=path)
    return True  # identity stays in the owner store, never emitted


def configure(*, active: bool, mode: str = "automatic_quota", authorization_reference: str | None = None, native_model: str | None = None, requested_owner: str = "personal"):
    """Reversible explicit policy switch, after identity/reference readiness checks."""
    from hermes_cli import auth
    if profile_name_for_home(get_hermes_home()) != "professor":
        raise ValueError("Account fallback policy is Professor-only")
    with auth._auth_store_lock():
        store = auth._load_auth_store()
        rows = store.get("credential_pool", {}).get("anthropic", [])
        if active:
            owned = [row for row in rows if _account_row(row)]
            refs = [row for row in rows if row.get("source") == SOURCE]
            if mode not in {"automatic_quota", "user_override"}:
                raise ValueError("Unknown account fallback mode")
            if mode == "user_override":
                refs = [r for r in refs if r.get("owner_profile") == requested_owner and native_model in r.get("verified_models", [])]
                if requested_owner not in {"studio", "personal"} or not authorization_reference or not native_model or not refs or any(native_model not in r.get("verified_models", []) for r in refs):
                    raise ValueError("Personal override requires direct authorization and verified native model references")
            if mode == "automatic_quota" and (not owned or any(identity(row) is None for row in owned) or not refs):
                raise ValueError("Policy requires verified Professor accounts and explicit owner references")
            from agent.credential_pool import PooledCredential
            for row in refs:
                if not row.get("verified_models"):
                    raise ValueError("Owner reference lacks exact model verification")
                _read_owner(PooledCredential.from_dict("anthropic", row))
        store["oauth_account_fallback"] = {"version": 1, "enabled": bool(active), "mode": mode}
        if active and mode == "user_override":
            store["oauth_account_fallback"].update(requested_owner=requested_owner, authorization_reference=authorization_reference,
                native_model=native_model, runtime_provider="anthropic", runtime_api_mode="anthropic_messages")
        auth._save_auth_store(store)


def native_override_route(*, current_provider: str, current_model: str, verified_catalog: list[str]):
    """Explicit current Opus adaptation; no guessed entitlement or lesser model."""
    if current_provider == "anthropic":
        native = current_model
    elif current_provider == "openrouter":
        native = {"anthropic/claude-opus-5.5": "claude-opus-5-5"}.get(current_model)
    else:
        native = None
    if not native or native not in verified_catalog:
        raise ValueError("Current model lacks verified native OAuth adaptation")
    return {"provider": "anthropic", "default": native, "api_mode": "anthropic_messages", "base_url": "https://api.anthropic.com"}


def apply_config_override(config: dict) -> dict:
    """In-memory routing overlay for a previously explicit operator override.

    Workgroup, home, cwd, session, tools and storage never change. Automatic quota
    mode does not adapt providers. Existing frozen sessions need a safe new launch.
    """
    import copy
    from hermes_cli import auth
    if profile_name_for_home(get_hermes_home()) != "professor":
        return config
    policy = auth._load_auth_store().get("oauth_account_fallback", {})
    if not (policy.get("enabled") is True and policy.get("version") == 1 and policy.get("mode") == "user_override"
            and policy.get("requested_owner") in {"studio", "personal"} and policy.get("authorization_reference") and policy.get("native_model")):
        return config
    model = policy["native_model"]
    rows = auth._load_auth_store().get("credential_pool", {}).get("anthropic", [])
    refs = [row for row in rows if row.get("source") == SOURCE and row.get("owner_profile") == policy.get("requested_owner") and model in row.get("verified_models", [])]
    if not refs:
        raise ValueError("Directed personal override has no verified owner reference")
    # Selection/hydration independently verifies identity, expiry and removal.
    from hermes_cli import managed_scope
    desired = {"provider": "anthropic", "default": model, "api_mode": "anthropic_messages", "base_url": "https://api.anthropic.com"}
    existing_model = config.get("model") if isinstance(config.get("model"), dict) else {}
    managed_keys = managed_scope.managed_config_keys()
    for key, value in desired.items():
        if ("model" in managed_keys or f"model.{key}" in managed_keys) and existing_model.get(key) != value:
            raise ValueError("Personal account override conflicts with managed model settings")
    for key in ("api_key", "api", "key_env"):
        if ("model" in managed_keys or f"model.{key}" in managed_keys) and existing_model.get(key):
            raise ValueError("Personal account override cannot remove managed authentication settings")
    for key in ("fallback_model", "fallback_providers"):
        if any(path == key or path.startswith(key + ".") for path in managed_keys) and config.get(key):
            raise ValueError("Personal account override conflicts with managed fallback settings")
    result = copy.deepcopy(config)
    result["model"] = {**(result.get("model") if isinstance(result.get("model"), dict) else {}), "provider": "anthropic", "default": model,
                       "api_mode": "anthropic_messages", "base_url": "https://api.anthropic.com"}
    # An old gateway credential must not win over selected OAuth authentication.
    result["model"].pop("api_key", None)
    result["model"].pop("api", None)
    result["model"].pop("key_env", None)
    result["fallback_model"] = []
    result["fallback_providers"] = []
    return result
