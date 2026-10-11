"""Real-import owner/reference integration with synthetic isolated OAuth stores."""
import json
import os
from collections import OrderedDict
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent import credential_pool as cp
from agent import oauth_account_fallback as fallback
from agent import anthropic_credentials as ac
from hermes_cli import auth

MODEL = "claude-test-exact"


def grant(ident, token=None):
    token = token or f"sk-ant-oat01-{ident}-synthetic"
    row = {"id": ident, "label": ident, "source": "manual:hermes_pkce", "auth_type": "oauth",
           "priority": 0, "access_token": token, "refresh_token": f"synthetic-refresh-{ident}",
           "expires_at_ms": int((time.time() + 7200) * 1000)}
    row["oauth_account_identity"] = fallback.identity_from_exchange({
        "access_token": token, "account": {"uuid": ident}, "organization": {"uuid": ident + "-org"}})
    return row


def write(path, rows, **extra):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"providers": {}, "credential_pool": {"anthropic": rows}, **extra}))


@pytest.fixture
def stores(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "_SPENT_ROTATION_FINGERPRINTS", OrderedDict())
    root = tmp_path / "hermes"
    professor = root / "profiles" / "professor"
    studio = root / "profiles" / "studio"
    for home in (root, professor, studio):
        home.mkdir(parents=True)
        (home / "config.yaml").write_text("model:\n  provider: anthropic\n")
    monkeypatch.setenv("HERMES_HOME", str(professor))
    monkeypatch.setattr(cp, "_seed_from_singletons", lambda *a: (False, set()))
    monkeypatch.setattr(cp, "_seed_from_env", lambda *a: (False, set()))
    monkeypatch.setattr(auth, "heal_forked_single_use_oauth_grants", lambda *a: None)
    write(professor / "auth.json", [grant("prof")], oauth_account_fallback={"version": 1, "enabled": True})
    write(studio / "auth.json", [grant("studio")])
    write(root / "auth.json", [grant("personal")])
    fallback.add_reference(owner="studio", credential_id="studio", models=[MODEL])
    fallback.add_reference(owner="personal", credential_id="personal", models=[MODEL])
    return root, professor, studio


def quota(pool, entry="prof", model=MODEL):
    return pool.mark_exhausted_and_rotate(status_code=429, credential_id=entry, model=model,
        error_context={"account_quota_reason": "usage_limit_reached", "account_quota_reset_at": time.time() + 1800})


def test_healthy_primary_never_hydrates_owner(stores, monkeypatch):
    pool = cp.load_pool("anthropic")
    monkeypatch.setattr(fallback, "hydrate", lambda *a, **k: pytest.fail("healthy primary touched donor"))
    assert pool.select(model=MODEL).id == "prof"


def test_quota_admits_studio_then_personal_preserving_scope(stores, monkeypatch):
    _, professor, studio = stores
    before = dict(os.environ)
    pool = cp.load_pool("anthropic")
    selected = quota(pool)
    assert selected.source == fallback.SOURCE
    assert selected.extra["owner_profile"] == "studio"
    selected = quota(pool, selected.id)
    assert selected.extra["owner_profile"] == "personal"
    assert selected.provider == "anthropic"
    assert os.environ == before
    assert auth._auth_file_path() == professor / "auth.json"
    disk = (professor / "auth.json").read_text()
    assert "synthetic-refresh-studio" not in disk and "synthetic-refresh-personal" not in disk
    refs = [r for r in json.loads(disk)["credential_pool"]["anthropic"] if r["source"] == fallback.SOURCE]
    assert all("access_token" not in r and "refresh_token" not in r and "secret_fingerprint" not in r for r in refs)
    assert json.loads((studio / "auth.json").read_text())["credential_pool"]["anthropic"][0]["account_quota"]["reason"] == "usage_limit_reached"


@pytest.mark.parametrize("status,context", [(429, {"reason": "rate_limit_error", "reset_at": time.time()+3600}),
    (401, {}), (500, {}), (429, {"account_quota_reason": "usage_limit_reached"}),
    (402, {"account_quota_reason": "billing_error", "account_quota_reset_at": time.time()+3600})])
def test_other_failures_never_admit_donor(stores, status, context):
    pool = cp.load_pool("anthropic")
    result = pool.mark_exhausted_and_rotate(status_code=status, credential_id="prof", model=MODEL, error_context=context)
    assert result is None or result.source != fallback.SOURCE


def test_duplicate_account_is_benched_as_one(stores):
    _, professor, _ = stores
    store = json.loads((professor / "auth.json").read_text())
    duplicate = grant("prof", token="sk-ant-oat01-second-prof-synthetic")
    duplicate["id"] = "duplicate"
    store["credential_pool"]["anthropic"].append(duplicate)
    (professor / "auth.json").write_text(json.dumps(store))
    pool = cp.load_pool("anthropic")
    assert quota(pool).source == fallback.SOURCE
    assert all(e.last_status == "exhausted" for e in pool.entries() if e.source != fallback.SOURCE)


def test_identity_missing_or_changed_blocks_reference(stores):
    _, _, studio = stores
    pool = cp.load_pool("anthropic")
    row = json.loads((studio / "auth.json").read_text())["credential_pool"]["anthropic"][0]
    row["access_token"] = "different-account-unverified"
    write(studio / "auth.json", [row])
    selected = quota(pool)
    assert selected.extra["owner_profile"] == "personal"
    pool = cp.load_pool("anthropic")
    prof = pool._find(lambda e: e.id == "prof")
    prof.extra.pop("oauth_account_identity")
    pool._persist()
    assert pool.select(model=MODEL) is None


def test_model_and_api_key_are_not_substituted(stores):
    pool = cp.load_pool("anthropic")
    pool.add_entry(cp.PooledCredential("anthropic", "api", "api", "api_key", 0, "manual", "sk-ant-api-synthetic"))
    assert quota(pool, model="other-model") is None
    assert pool.select(model="other-model") is None


def test_owner_refresh_is_once_across_consumers_and_commit_before_use(stores, monkeypatch):
    _, _, studio = stores
    pool = cp.load_pool("anthropic")
    ref = quota(pool)
    path = studio / "auth.json"
    store = json.loads(path.read_text())
    store["credential_pool"]["anthropic"][0]["expires_at_ms"] = 1
    path.write_text(json.dumps(store))
    calls = []
    def refresh(token, **kwargs):
        calls.append(token)
        time.sleep(.05)
        return {"access_token": "sk-ant-oat01-rotated", "refresh_token": "rotated-refresh", "expires_at_ms": int((time.time()+7200)*1000)}
    monkeypatch.setattr(ac, "refresh_anthropic_oauth_pure", refresh)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: fallback.hydrate(ref), range(2)))
    assert len(calls) == 1
    assert all(r.access_token == "sk-ant-oat01-rotated" and r.refresh_token is None for r in results)
    owner = json.loads(path.read_text())["credential_pool"]["anthropic"][0]
    assert owner["refresh_token"] == "rotated-refresh" and fallback.identity(owner)


def test_failed_commit_quarantines_pair_and_never_replays(stores, monkeypatch):
    _, _, studio = stores
    pool = cp.load_pool("anthropic")
    ref = quota(pool)
    store = json.loads((studio / "auth.json").read_text())
    store["credential_pool"]["anthropic"][0]["expires_at_ms"] = 1
    (studio / "auth.json").write_text(json.dumps(store))
    calls = []
    monkeypatch.setattr(ac, "refresh_anthropic_oauth_pure", lambda *a, **k: calls.append(a) or
        {"access_token": "never-return", "refresh_token": "never-return-refresh", "expires_at_ms": 9999999999999})
    monkeypatch.setattr(auth, "_save_auth_store", lambda *a, **k: (_ for _ in ()).throw(OSError("synthetic write failure")))
    with pytest.raises(ValueError, match="commit failed"):
        fallback.hydrate(ref)
    with pytest.raises(ValueError, match="reauthentication"):
        fallback.hydrate(ref)
    assert len(calls) == 1
    assert "never-return" not in (studio / "auth.json").read_text()


def test_removed_reference_not_reseeded_and_owner_removal_observed(stores):
    _, _, studio = stores
    pool = cp.load_pool("anthropic")
    ref = quota(pool)
    pool.remove_index(next(i for i,e in enumerate(pool.entries(),1) if e.id == ref.id))
    assert ref.id not in [e.id for e in cp.load_pool("anthropic").entries()]
    write(studio / "auth.json", [])
    with pytest.raises(ValueError, match="removed"):
        fallback.hydrate(ref)


def test_reset_and_elapsed_window_restore_professor(stores):
    pool = cp.load_pool("anthropic")
    quota(pool)
    pool.reset_statuses()
    assert pool.select(model=MODEL).id == "prof"
    quota(pool)
    prof = pool._find(lambda e: e.id == "prof")
    prof.extra["account_quota"]["reset_at"] = time.time()-1
    pool._adopt(prof, last_error_reset_at=time.time()-1)
    assert pool.select(model=MODEL).id == "prof"


def test_structured_error_extraction_and_auxiliary_rotation(stores, monkeypatch):
    from agent.agent_runtime_helpers import extract_api_error_context
    from agent import auxiliary_client as aux
    error = type("Error", (Exception,), {"status_code":429, "body":{"error":{
        "type":"usage_limit_reached", "reset_at":time.time()+1800}}, "response":None})("do not persist this raw message")
    context = extract_api_error_context(error)
    assert context["account_quota_reason"] == "usage_limit_reached"
    assert aux._recover_provider_pool("anthropic", error, failed_api_key=grant("prof")["access_token"], model=MODEL)
    assert cp.load_pool("anthropic").select(model=MODEL).source == fallback.SOURCE


def test_other_profiles_and_disabled_policy_never_hydrate(stores, monkeypatch):
    root, professor, _ = stores
    store = json.loads((professor / "auth.json").read_text())
    store["oauth_account_fallback"]["enabled"] = False
    (professor / "auth.json").write_text(json.dumps(store))
    assert not fallback.enabled("anthropic")
    pool = cp.load_pool("anthropic")
    assert quota(pool) is None
    monkeypatch.setenv("HERMES_HOME", str(root))
    assert not fallback.enabled("anthropic")


def test_new_unknown_primary_and_peer_reset_prevent_stale_admission(stores):
    _, professor, _ = stores
    pool = cp.load_pool("anthropic")
    quota(pool)
    peer = cp.load_pool("anthropic")
    peer.reset_statuses()
    assert pool.select(model=MODEL).id == "prof"
    quota(pool)
    store = json.loads((professor / "auth.json").read_text())
    unknown = grant("unverified")
    unknown.pop("oauth_account_identity")
    store["credential_pool"]["anthropic"].append(unknown)
    (professor / "auth.json").write_text(json.dumps(store))
    assert pool.select(model=MODEL) is None


def test_later_auth_failure_invalidates_quota_admission(stores):
    pool = cp.load_pool("anthropic")
    quota(pool)
    pool.mark_exhausted_and_rotate(status_code=401, credential_id="prof", model=MODEL,
                                   error_context={"reason":"authentication_error"})
    assert pool.select(model=MODEL) is None


def test_main_recovery_keeps_exact_route_and_professor_context(stores):
    from types import SimpleNamespace
    from agent import agent_runtime_helpers as runtime
    pool = cp.load_pool("anthropic")
    initial = pool.select(model=MODEL)
    agent = SimpleNamespace(_credential_pool=pool, _credential_pool_entry_id=initial.id,
        provider="anthropic", model=MODEL, base_url="https://api.anthropic.com", api_key=initial.access_token,
        api_mode="anthropic_messages", profile="professor", workgroup="professor", cwd="/Volumes/ACC-Private/synthetic",
        session_id="synthetic-professor-session", tools=["synthetic-vault-tool"])
    scope = (agent.provider, agent.model, agent.base_url, agent.api_mode, agent.profile,
             agent.workgroup, agent.cwd, agent.session_id, list(agent.tools))
    def swap(entry):
        agent.api_key = entry.access_token
        agent._credential_pool_entry_id = entry.id
        return True
    agent._swap_credential = swap
    recovered, _ = runtime.recover_with_credential_pool(agent, status_code=429, has_retried_429=True,
        error_context={"account_quota_reason":"usage_limit_reached", "account_quota_reset_at":time.time()+1800})
    assert recovered
    assert (agent.provider, agent.model, agent.base_url, agent.api_mode, agent.profile,
            agent.workgroup, agent.cwd, agent.session_id, agent.tools) == scope
    assert pool.current().extra["owner_profile"] == "studio"


def test_enabled_resolver_cannot_bypass_gate_with_ambient_key(stores, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api-synthetic-forbidden")
    pool = cp.load_pool("anthropic")
    quota(pool, model="not-verified")
    assert ac.resolve_anthropic_token(model="not-verified") is None


def test_pkce_identity_is_bound_to_provider_response_and_generation():
    state = ac._oauth_token_state({"access_token":"synthetic-generation", "refresh_token":"synthetic-refresh",
        "account":{"uuid":"account"}, "organization":{"uuid":"org"}})
    assert fallback.identity(state) == ("account", "org")
    state["access_token"] = "different"
    assert fallback.identity(state) is None
    assert fallback.identity_from_exchange({"access_token":"synthetic", "label":"personal"}) is None


def test_existing_account_enrollment_uses_authenticated_profile_not_labels(stores, monkeypatch):
    import urllib.request
    from io import BytesIO
    _, professor, _ = stores
    store = json.loads((professor / "auth.json").read_text())
    store["credential_pool"]["anthropic"][0].pop("oauth_account_identity")
    (professor / "auth.json").write_text(json.dumps(store))
    requests = []
    def profile(req, **kwargs):
        requests.append(req)
        return BytesIO(json.dumps({"account":{"uuid":"prof"}, "organization":{"uuid":"prof-org"}}).encode())
    monkeypatch.setattr(urllib.request, "urlopen", profile)
    assert fallback.verify_pool_account("prof") is True
    assert requests[0].full_url == "https://api.anthropic.com/api/oauth/profile"
    owner_row = json.loads((professor / "auth.json").read_text())["credential_pool"]["anthropic"][0]
    assert fallback.identity(owner_row) == ("prof", "prof-org")
    assert owner_row["access_token"] == grant("prof")["access_token"]
    assert quota(cp.load_pool("anthropic")).source == fallback.SOURCE


def test_verifier_refuses_generation_change_and_redacts_error(stores, monkeypatch):
    import urllib.request
    from io import BytesIO
    _, professor, _ = stores
    def changed(req, **kwargs):
        store = json.loads((professor / "auth.json").read_text())
        store["credential_pool"]["anthropic"][0]["access_token"] = "other-generation"
        (professor / "auth.json").write_text(json.dumps(store))
        return BytesIO(json.dumps({"account":{"uuid":"prof"}, "organization":{"uuid":"prof-org"}}).encode())
    monkeypatch.setattr(urllib.request, "urlopen", changed)
    with pytest.raises(ValueError, match="changed during"):
        fallback.verify_pool_account("prof")
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(ValueError("private raw provider detail")))
    with pytest.raises(ValueError) as err:
        fallback.verify_pool_account("prof")
    assert "private raw" not in str(err.value)


def test_explicit_policy_reversible_and_checks_readiness(stores):
    _, professor, _ = stores
    fallback.configure(active=False)
    assert not fallback.enabled("anthropic")
    fallback.configure(active=True)
    assert fallback.enabled("anthropic")
    store = json.loads((professor / "auth.json").read_text())
    store["credential_pool"]["anthropic"][0].pop("oauth_account_identity")
    (professor / "auth.json").write_text(json.dumps(store))
    with pytest.raises(ValueError, match="verified Professor"):
        fallback.configure(active=True)
    fallback.configure(active=False)
    assert not fallback.enabled("anthropic")


def test_two_processes_refresh_one_owner_generation(stores):
    import os
    import subprocess
    import sys
    _, professor, studio = stores
    ref = quota(cp.load_pool("anthropic"))
    path = studio / "auth.json"
    store = json.loads(path.read_text())
    store["credential_pool"]["anthropic"][0]["expires_at_ms"] = 1
    path.write_text(json.dumps(store))
    counter = studio / "synthetic-refresh-count.txt"
    script = '''
import json, os, time
from pathlib import Path
from agent.credential_pool import PooledCredential
from agent import anthropic_credentials as ac
from agent.oauth_account_fallback import hydrate
home = Path(os.environ['HERMES_HOME'])
rows = json.loads((home/'auth.json').read_text())['credential_pool']['anthropic']
ref = next(r for r in rows if r.get('owner_profile') == 'studio')
def refresh(*args, **kwargs):
    with (home.parent/'studio'/'synthetic-refresh-count.txt').open('a') as count:
        count.write('refresh\\n')
    time.sleep(.1)
    return {'access_token':'sk-ant-oat01-process-rotated', 'refresh_token':'process-rotated-refresh',
            'expires_at_ms':int((time.time()+7200)*1000)}
ac.refresh_anthropic_oauth_pure = refresh
entry = hydrate(PooledCredential.from_dict('anthropic', ref), model='claude-test-exact')
assert entry.access_token == 'sk-ant-oat01-process-rotated'
assert entry.refresh_token is None
'''
    env = {k:v for k,v in os.environ.items() if not k.endswith(("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD"))}
    env["HERMES_HOME"] = str(professor)
    processes = [subprocess.Popen([sys.executable, "-c", script], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(2)]
    for process in processes:
        _, stderr = process.communicate(timeout=20)
        assert process.returncode == 0, stderr.decode()
    assert counter.read_text().splitlines() == ["refresh"]


def test_actual_client_swap_preserves_professor_scope_and_transport(stores):
    from types import SimpleNamespace
    from agent.client_lifecycle import ClientLifecycleMixin
    ref = quota(cp.load_pool("anthropic"))
    agent = SimpleNamespace(provider="anthropic", model=MODEL, base_url="https://api.anthropic.com",
        api_mode="anthropic_messages", profile="professor", workgroup="professor", cwd="/Volumes/ACC-Private/synthetic",
        session_id="synthetic", tools=["synthetic-vault-tool"], _anthropic_client=SimpleNamespace(close=lambda:None),
        _build_direct_anthropic_client=lambda token, base: SimpleNamespace(base_url=base),
        _anthropic_oauth_flag=lambda token: True)
    assert ClientLifecycleMixin._swap_credential(agent, ref)
    assert agent.provider == "anthropic" and agent.model == MODEL
    assert agent.base_url == "https://api.anthropic.com" and agent.api_mode == "anthropic_messages"
    assert agent.profile == agent.workgroup == "professor"
    assert agent.cwd == "/Volumes/ACC-Private/synthetic" and agent.tools == ["synthetic-vault-tool"]


def test_same_account_different_organization_is_not_a_donor(stores):
    _, _, studio = stores
    row = grant("prof", token="sk-ant-oat01-same-account-other-org")
    row["id"] = "studio"
    row["oauth_account_identity"]["organization_id"] = "different-org"
    write(studio / "auth.json", [row])
    # Reenrollment binds the authoritative same-account identity. That account
    # must still be rejected as a donor even though organization/token differs.
    pool = cp.load_pool("anthropic")
    old = next(e for e in pool.entries() if e.extra.get("owner_profile") == "studio")
    pool.remove_index(next(i for i,e in enumerate(pool.entries(),1) if e.id == old.id))
    fallback.add_reference(owner="studio", credential_id="studio", models=[MODEL])
    assert quota(cp.load_pool("anthropic")).extra["owner_profile"] == "personal"


def test_exact_token_alias_cannot_overwrite_account_quota_evidence(stores):
    _, professor, _ = stores
    store = json.loads((professor / "auth.json").read_text())
    alias = dict(store["credential_pool"]["anthropic"][0], id="same-token-alias")
    store["credential_pool"]["anthropic"].append(alias)
    (professor / "auth.json").write_text(json.dumps(store))
    assert quota(cp.load_pool("anthropic")).source == fallback.SOURCE


def test_auxiliary_resolver_uses_selected_oauth_and_exact_model(stores, monkeypatch):
    from agent import auxiliary_client as aux
    from agent import anthropic_adapter as adapter
    pool = cp.load_pool("anthropic")
    quota(pool)
    built = []
    monkeypatch.setattr(adapter, "build_anthropic_client", lambda token, base:
        built.append((token,base)) or object())
    client, model = aux._try_anthropic(explicit_api_key="sk-ant-api-synthetic-forbidden", model=MODEL)
    assert client is not None and model == MODEL
    assert built[0][0] == grant("studio")["access_token"]
    assert built[0][1].rstrip("/") == "https://api.anthropic.com"


def test_generation_merge_keeps_identity_binding_with_new_pair(stores):
    _, professor, _ = stores
    old = grant("prof")
    store = json.loads((professor / "auth.json").read_text())
    rotated = grant("prof", token="sk-ant-oat01-current-rotated-prof")
    store["credential_pool"]["anthropic"][0] = rotated
    (professor / "auth.json").write_text(json.dumps(store))
    auth.write_credential_pool("anthropic", [old], token_bases={"prof":(old["access_token"],old["refresh_token"])})
    row = json.loads((professor / "auth.json").read_text())["credential_pool"]["anthropic"][0]
    assert row["access_token"] == rotated["access_token"]
    assert fallback.identity(row) == ("prof", "prof-org")
