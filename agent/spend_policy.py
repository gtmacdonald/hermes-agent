"""Exact model admission at the final metered HTTP dispatch boundary."""
import json
import math
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

POLICY_PATH = Path.home() / '.config/model-spend-policy.json'
HOSTS = {'ai-gateway.vercel.sh': 'vercel', 'openrouter.ai': 'openrouter', 'api.openai.com': 'openai-platform-disabled'}

class SpendDenied(ValueError):
    pass

def require(value):
    if not value:
        raise SpendDenied("invalid model spend policy")

def lane(url):
    u = urlsplit(str(url))
    host = (u.hostname or '').lower()
    name = HOSTS.get(host)
    if host.endswith('.openrouter.ai'):
        raise SpendDenied('unverified OpenRouter regional/alternate origin denied')
    if name and (u.scheme != 'https' or u.port not in (None, 443) or u.username or u.password or u.query or u.fragment):
        raise SpendDenied('metered route requires its exact HTTPS origin')
    return name

def prepare(url, body, *, policy_path=None):
    name = lane(url)
    if not name:
        return body
    try:
        cfg = json.loads(Path(policy_path or POLICY_PATH).read_text())
        require(cfg['version'] == 1 and cfg['enforcement'] == 'deny')
        require(date.today() < date.fromisoformat(cfg['expires_on']))
        limits = cfg['limits']
        for key in ('max_output_tokens', 'max_body_bytes'):
            require(type(limits[key]) is int and limits[key] > 0)
        spec = cfg['lanes'][name]['models'][body['model']]
        require(isinstance(body, dict) and isinstance(spec, dict))
        require(isinstance(spec['providers'], list) and bool(spec['providers']))
        require(all(isinstance(p, str) and p.strip() == p and bool(p) for p in spec['providers']))
        require(date.today() < date.fromisoformat(spec['expires_on']))
        for key in ('input_usd_per_million', 'output_usd_per_million'):
            price = spec[key]
            require(isinstance(price, (int, float)) and not isinstance(price, bool))
            require(math.isfinite(price) and 0 <= price <= cfg['price_ceiling'][key])
        require(spec['source'].startswith('https://') and spec['checked_on'])
        require(urlsplit(str(url)).path in spec['paths'])
        require(len(json.dumps(body).encode()) <= limits['max_body_bytes'])
    except (OSError, KeyError, ValueError, TypeError, AssertionError):
        raise SpendDenied('model spend policy denied: missing, invalid, expired or unlisted route/model') from None
    # These fields can select extra models, paid server tools or alternate price classes.
    for field in ('models', 'plugins', 'web_search_options', 'search_parameters', 'modalities', 'audio', 'service_tier', 'routing', 'transforms'):
        if field in body:
            raise SpendDenied('model spend policy denied: routing or paid option')
    for tool in body.get('tools', []):
        if not isinstance(tool, dict) or tool.get('type') != 'function':
            raise SpendDenied('paid native tools denied')
    for msg in body.get('messages', []):
        content = msg.get('content')
        if isinstance(content, list) and any(not isinstance(part, dict) or part.get('type') != 'text' for part in content):
            raise SpendDenied('priced media input denied')
    def text_input(value):
        if isinstance(value, list):
            return all(text_input(v) for v in value)
        if isinstance(value, dict):
            if 'type' in value and value['type'] not in ('message', 'text', 'input_text', 'output_text', 'function_call', 'function_call_output', 'reasoning'):
                return False
            return all(text_input(v) for v in value.values())
        return True
    if 'input' in body and not text_input(body['input']):
        raise SpendDenied('priced or unsupported Responses input denied')
    out = dict(body)
    opts = out.get('providerOptions', {})
    if opts:
        gw = opts.get('gateway', {}) if isinstance(opts, dict) else None
        if set(opts) != {'gateway'} or not isinstance(gw, dict) or any(k not in ('only', 'zeroDataRetention') for k in gw):
            raise SpendDenied('model spend policy denied: caller provider options')
        if gw.get('only') and not set(gw['only']).issubset(spec['providers']):
            raise SpendDenied('provider override outside verified provider set')
    if spec.get('evaluation'):
        if 'state' not in body or 'questions' not in body:
            raise SpendDenied('Jev is permitted only through its evaluation API')
    else:
        # Preserve lower caller caps; omitted and larger caps receive the configured bound.
        fields = [k for k in ('max_tokens', 'max_completion_tokens', 'max_output_tokens') if k in out]
        if not fields:
            fields = ['max_output_tokens' if urlsplit(str(url)).path.endswith('/responses') else 'max_tokens']
        for key in fields:
            value = out.get(key, limits['max_output_tokens'])
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise SpendDenied('invalid output token cap')
            out[key] = min(value, limits['max_output_tokens'])
    if name == 'openrouter':
        provider = out.get('provider', {})
        if not isinstance(provider, dict) or any(k not in ('only', 'order', 'data_collection', 'zdr', 'allow_fallbacks', 'require_parameters', 'max_price', 'sort', 'ignore') for k in provider):
            raise SpendDenied('unsupported provider override')
        if provider.get('only') and not set(provider['only']).issubset(spec['providers']):
            raise SpendDenied('provider override outside verified provider set')
        caps = {'prompt': spec['input_usd_per_million'], 'completion': spec['output_usd_per_million'], 'request': 0}
        caller_caps = provider.get('max_price', {})
        if not isinstance(caller_caps, dict) or any(k not in caps for k in caller_caps):
            raise SpendDenied('unsupported caller price cap')
        for key, value in caller_caps.items():
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
                raise SpendDenied('invalid caller price cap')
            caps[key] = min(value, caps[key])
        out['provider'] = {**provider, 'only': provider.get('only') or spec['providers'], 'allow_fallbacks': False,
                           'max_price': caps}
    elif not spec.get('evaluation'):
        if 'provider' in out:
            raise SpendDenied('caller provider override denied')
        out['providerOptions'] = {'gateway': {**opts.get('gateway', {}), 'only': opts.get('gateway', {}).get('only') or spec['providers']}}
    if spec.get('require_zdr'):
        if name == 'openrouter':
            out['provider']['zdr'] = True
            out['provider']['data_collection'] = 'deny'
        elif not spec.get('evaluation'):
            out['providerOptions']['gateway']['zeroDataRetention'] = True
    return out

def httpx_hook(request):
    if lane(request.url) and request.method not in ('GET', 'HEAD'):
        import httpx
        try:
            body = json.loads(request.read())
        except (ValueError, TypeError):
            raise SpendDenied('metered request must be a model-bearing JSON object') from None
        raw = json.dumps(prepare(request.url, body)).encode()
        request._content = raw
        request.stream = httpx.ByteStream(raw)
        request.headers['Content-Length'] = str(len(raw))

async def async_httpx_hook(request):
    await request.aread()
    httpx_hook(request)

def guard_client(client, *, async_mode=False):
    """Attach to the actual SDK HTTP client, including caller-supplied transports."""
    hooks = client._client.event_hooks.setdefault('request', [])
    hook = async_httpx_hook if async_mode else httpx_hook
    if hook not in hooks:
        hooks.append(hook)
    # SDK preparation is outside its transport retry catch. Surface a policy refusal
    # directly rather than disguising it as a retryable connection failure.
    if not getattr(client, '_spend_preparation_guarded', False):
        prior = client._prepare_request
        if async_mode:
            async def checked(request):
                await prior(request)
                await async_httpx_hook(request)
        else:
            def checked(request):
                prior(request)
                httpx_hook(request)
        client._prepare_request = checked
        client._spend_preparation_guarded = True
    return client
