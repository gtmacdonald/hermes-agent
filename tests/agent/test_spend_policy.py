"""Fork-local spending policy: metered lanes admit only reviewed models, fail closed otherwise."""
from datetime import date, timedelta
import json

import httpx
import openai
import pytest

from agent import spend_policy as sp

pytestmark = pytest.mark.real_spend_policy

OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
VERCEL = "https://ai-gateway.vercel.sh/v1/chat/completions"
MODEL = "google/gemini-2.5-flash-lite"


def _policy(tmp_path, *, expires=None, enforcement="deny", require_zdr=False):
    expires = expires or (date.today() + timedelta(days=7)).isoformat()
    spec = {"providers": ["google-vertex"], "expires_on": expires, "input_usd_per_million": 0.1,
            "output_usd_per_million": 0.4, "source": "https://example.com/pricing",
            "checked_on": "2026-10-05", "paths": ["/api/v1/chat/completions", "/v1/chat/completions"],
            "require_zdr": require_zdr}
    cfg = {"version": 1, "enforcement": enforcement, "expires_on": expires,
           "limits": {"max_output_tokens": 4096, "max_body_bytes": 200_000},
           "max_usd_per_million_tokens": 4.0,
           "lanes": {"openrouter": {"models": {MODEL: dict(spec)}}, "vercel": {"models": {MODEL: dict(spec)}}}}
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(cfg))
    return path


def _body(**extra):
    return {"model": MODEL, "messages": [{"role": "user", "content": "hi"}], **extra}


def test_unmetered_hosts_pass_through_untouched(tmp_path):
    body = _body(max_tokens=10**6)
    assert sp.prepare("http://192.168.1.20:8080/v1/chat/completions", body,
                      policy_path=tmp_path / "missing.json") is body


@pytest.mark.parametrize("url", [
    "http://openrouter.ai/api/v1/chat/completions",
    "https://openrouter.ai:8443/api/v1/chat/completions",
    "https://eu.openrouter.ai/api/v1/chat/completions",
])
def test_metered_lane_requires_its_exact_https_origin(tmp_path, url):
    with pytest.raises(sp.SpendDenied):
        sp.prepare(url, _body(), policy_path=_policy(tmp_path))


def test_missing_expired_or_non_enforcing_policy_denies(tmp_path):
    with pytest.raises(sp.SpendDenied):
        sp.prepare(OPENROUTER, _body(), policy_path=tmp_path / "missing.json")
    with pytest.raises(sp.SpendDenied):
        sp.prepare(OPENROUTER, _body(), policy_path=_policy(tmp_path, expires=date.today().isoformat()))
    with pytest.raises(sp.SpendDenied):
        sp.prepare(OPENROUTER, _body(), policy_path=_policy(tmp_path, enforcement="warn"))


def test_unlisted_model_denied(tmp_path):
    with pytest.raises(sp.SpendDenied):
        sp.prepare(OPENROUTER, dict(_body(), model="anthropic/claude-opus-5.5"), policy_path=_policy(tmp_path))


def test_openrouter_admission_caps_tokens_and_pins_providers(tmp_path):
    out = sp.prepare(OPENROUTER, _body(max_tokens=100_000), policy_path=_policy(tmp_path, require_zdr=True))
    assert out["max_tokens"] == 4096
    assert out["provider"]["only"] == ["google-vertex"] and out["provider"]["allow_fallbacks"] is False
    assert out["provider"]["max_price"] == {"prompt": 0.1, "completion": 0.4, "request": 0}
    assert out["provider"]["zdr"] is True and out["provider"]["data_collection"] == "deny"
    # A lower caller cap is preserved.
    assert sp.prepare(OPENROUTER, _body(max_tokens=50), policy_path=_policy(tmp_path))["max_tokens"] == 50


def test_vercel_admission_pins_gateway_providers(tmp_path):
    out = sp.prepare(VERCEL, _body(), policy_path=_policy(tmp_path, require_zdr=True))
    assert out["providerOptions"]["gateway"] == {"only": ["google-vertex"], "zeroDataRetention": True}
    assert out["max_tokens"] == 4096


@pytest.mark.parametrize("extra", [
    {"tools": [{"type": "web_search"}]},
    {"plugins": [{"id": "web"}]},
    {"models": [MODEL, "other/model"]},
    {"provider": {"only": ["someone-else"]}},
    {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}]},
])
def test_paid_options_routing_overrides_and_media_denied(tmp_path, extra):
    with pytest.raises(sp.SpendDenied):
        sp.prepare(OPENROUTER, _body(**extra), policy_path=_policy(tmp_path))


def test_guarded_sdk_client_rewrites_admitted_and_blocks_denied_requests(tmp_path, monkeypatch):
    monkeypatch.setattr(sp, "POLICY_PATH", _policy(tmp_path))
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "x", "object": "chat.completion", "created": 0, "model": MODEL,
                                         "choices": [{"index": 0, "finish_reason": "stop",
                                                      "message": {"role": "assistant", "content": "ok"}}]})

    client = sp.guard_client(openai.OpenAI(api_key="k", base_url="https://openrouter.ai/api/v1", max_retries=0,
                                           http_client=httpx.Client(transport=httpx.MockTransport(handler))))
    client.chat.completions.create(model=MODEL, messages=[{"role": "user", "content": "hi"}], max_tokens=10**6)
    assert sent[-1]["max_tokens"] == 4096 and sent[-1]["provider"]["allow_fallbacks"] is False
    with pytest.raises(sp.SpendDenied):
        client.chat.completions.create(model="unlisted/model", messages=[{"role": "user", "content": "hi"}])
    assert len(sent) == 1
    # Guarding twice does not stack hooks.
    assert sp.guard_client(client) is client
    assert client._client.event_hooks["request"].count(sp.httpx_hook) == 1


@pytest.mark.parametrize('input_rate,output_rate,allowed', [
    (0, 0, True), (3.9, 3.9, True), (4, .1, True), (.1, 4, True), (4, 4, True),
    (4.000001, .1, False), (.1, 4.000001, False), (4.1, 4.1, False),
    (-1, .1, False), (.1, -1, False), (True, .1, False), (.1, True, False),
    (float('nan'), .1, False), (.1, float('nan'), False),
    (float('inf'), .1, False), (.1, float('inf'), False),
    ('1', .1, False), (.1, '1', False), (None, .1, False), (.1, None, False),
])
def test_single_ceiling_uses_max_of_both_validated_rates(tmp_path, input_rate, output_rate, allowed):
    path = _policy(tmp_path)
    cfg = json.loads(path.read_text())
    for lane in cfg['lanes'].values():
        spec = lane['models'][MODEL]
        spec.update(input_usd_per_million=input_rate, output_usd_per_million=output_rate)
    path.write_text(json.dumps(cfg))
    for url in (OPENROUTER, VERCEL):
        if not allowed:
            with pytest.raises(sp.SpendDenied):
                sp.prepare(url, _body(), policy_path=path)
        else:
            out = sp.prepare(url, _body(), policy_path=path)
            assert out['max_tokens'] == cfg['limits']['max_output_tokens']
            if url == OPENROUTER:
                assert out['provider']['max_price'] == {'prompt': input_rate, 'completion': output_rate, 'request': 0}
                assert out['provider']['only'] == cfg['lanes']['openrouter']['models'][MODEL]['providers']
                assert out['provider']['allow_fallbacks'] is False
            else:
                assert out['providerOptions']['gateway']['only'] == cfg['lanes']['vercel']['models'][MODEL]['providers']


@pytest.mark.parametrize('ceiling', [-1, True, None, '4', float('nan'), float('inf'), {}, []])
def test_invalid_single_ceiling_denies_instead_of_using_legacy_limits(tmp_path, ceiling):
    path = _policy(tmp_path)
    cfg = json.loads(path.read_text())
    cfg['max_usd_per_million_tokens'] = ceiling
    cfg['price_ceiling'] = {'input_usd_per_million': 100, 'output_usd_per_million': 100}
    path.write_text(json.dumps(cfg))
    with pytest.raises(sp.SpendDenied):
        sp.prepare(VERCEL, _body(), policy_path=path)


@pytest.mark.parametrize('copies,allowed', [
    ({'input_usd_per_million': 4, 'output_usd_per_million': 4}, True),
    ({'input_usd_per_million': 3, 'output_usd_per_million': 4}, False),
    ({'input_usd_per_million': 4, 'output_usd_per_million': 3}, False),
    ({'input_usd_per_million': 5, 'output_usd_per_million': 4}, False),
    ({'input_usd_per_million': 4, 'output_usd_per_million': 5}, False),
    ({'input_usd_per_million': True, 'output_usd_per_million': 4}, False),
    ({'input_usd_per_million': 4, 'output_usd_per_million': '4'}, False),
    ({'input_usd_per_million': float('nan'), 'output_usd_per_million': 4}, False),
    ({'input_usd_per_million': 4, 'output_usd_per_million': float('inf')}, False),
    ({'input_usd_per_million': 4}, False),
    ({'input_usd_per_million': 4, 'output_usd_per_million': 4, 'extra': 4}, False),
    (None, False), ([], False), (4, False),
])
def test_legacy_compatibility_copies_must_match_authoritative_scalar(tmp_path, copies, allowed):
    path = _policy(tmp_path)
    cfg = json.loads(path.read_text())
    cfg['price_ceiling'] = copies
    path.write_text(json.dumps(cfg))
    for url in (VERCEL, OPENROUTER):
        if allowed:
            assert sp.prepare(url, _body(), policy_path=path)['max_tokens'] == cfg['limits']['max_output_tokens']
        else:
            with pytest.raises(sp.SpendDenied):
                sp.prepare(url, _body(), policy_path=path)


def test_legacy_only_policy_cannot_override_missing_authoritative_scalar(tmp_path):
    path = _policy(tmp_path)
    cfg = json.loads(path.read_text())
    del cfg['max_usd_per_million_tokens']
    cfg['price_ceiling'] = {'input_usd_per_million': 4, 'output_usd_per_million': 4}
    path.write_text(json.dumps(cfg))
    with pytest.raises(sp.SpendDenied):
        sp.prepare(VERCEL, _body(), policy_path=path)
