"""Core makes Codex-signed requests for plugins; the token never reaches plugin code.

Real loopback HTTP servers stand in for the provider; a real plugin directory under the test
HERMES_HOME makes the call, and a fake Codex sign-in is seeded in that home's auth store.
"""

import base64
import importlib.util
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from hermes_cli import plugin_provider_requests as ppr


def _seg(d: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()


def _jwt() -> str:
    seg = _seg
    return f"{seg({'alg': 'none'})}.{seg({'exp': int(time.time()) + 86400, 'sub': 'fake'})}.sig"


TOKEN = _jwt()


class _Stub:
    def __init__(self, status=200, location=""):
        self.seen: list[dict] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self):
                length = int(self.headers.get("Content-Length") or 0)
                stub.seen.append({"path": self.path, "headers": dict(self.headers),
                                  "body": self.rfile.read(length)})
                self.send_response(status)
                if location:
                    self.send_header("Location", location)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"value": "ek_ephemeral", "expires_at": 1}')

            do_GET = do_POST = _answer

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stubs(monkeypatch):
    allowed, other = _Stub(), _Stub()
    origins, resolve = ppr._PROVIDERS["openai-codex"]
    # Test-only: the loopback stub joins the real allowlist; ``other`` stays outside it.
    monkeypatch.setitem(ppr._PROVIDERS, "openai-codex",
                        (origins | {("http", "127.0.0.1", allowed.server.server_address[1])}, resolve))
    yield allowed, other
    allowed.close()
    other.close()


def _home() -> Path:
    return Path(os.environ["HERMES_HOME"])


def _sign_in():
    (_home() / "auth.json").write_text(json.dumps({"version": 1, "providers": {"openai-codex": {
        "auth_mode": "chatgpt", "tokens": {"access_token": TOKEN, "refresh_token": "fake-rt"}}}}))


def _plugin(declares: bool):
    """A dashboard-style plugin module (``dashboard/plugin_api.py``) loaded the way the dashboard does."""
    root = _home() / "plugins" / ("voice" if declares else "undeclared")
    (root / "dashboard").mkdir(parents=True)
    (root / "plugin.yaml").write_text("name: voice\n" + ("requires_auth: [openai-codex]\n" if declares else ""))
    (root / "dashboard" / "plugin_api.py").write_text(
        "from hermes_cli.plugin_provider_requests import credentialed_provider_request\n"
        "def mint(url):\n"
        "    return credentialed_provider_request('openai-codex', 'POST', url, json={'session': {}})\n")
    spec = importlib.util.spec_from_file_location(f"hermes_dashboard_plugin_{root.name}", root / "dashboard" / "plugin_api.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_token_attached_for_allowlisted_origin_and_never_returned(stubs):
    allowed, _ = stubs
    _sign_in()
    result = _plugin(declares=True).mint(allowed.url + "/v1/realtime/client_secrets")

    assert result.status == 200 and result.json()["value"] == "ek_ephemeral"
    assert allowed.seen[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert json.loads(allowed.seen[0]["body"]) == {"session": {}}
    assert TOKEN not in repr(result) and TOKEN not in json.dumps(result.headers)


def test_disallowed_origin_and_redirect_never_carry_the_token(stubs, monkeypatch):
    _, other = stubs
    _sign_in()
    plugin = _plugin(declares=True)
    with pytest.raises(PermissionError, match="Refusing"):
        plugin.mint(other.url + "/steal")
    assert other.seen == []

    redirecting = _Stub(status=302, location=other.url + "/steal")
    origins, resolve = ppr._PROVIDERS["openai-codex"]
    monkeypatch.setitem(ppr._PROVIDERS, "openai-codex",
                        (origins | {("http", "127.0.0.1", redirecting.server.server_address[1])}, resolve))
    try:
        result = plugin.mint(redirecting.url + "/v1/realtime/client_secrets")
    finally:
        redirecting.close()
    assert result.status == 302 and other.seen == []


def test_undeclared_plugin_and_non_plugin_callers_are_refused(stubs):
    allowed, _ = stubs
    _sign_in()
    with pytest.raises(PermissionError, match="requires_auth"):
        _plugin(declares=False).mint(allowed.url)
    with pytest.raises(PermissionError, match="installed plugin"):
        ppr.credentialed_provider_request("openai-codex", "GET", allowed.url)
    assert allowed.seen == []


def test_not_signed_in_names_the_sign_in_command(stubs):
    allowed, _ = stubs
    with pytest.raises(ppr.ProviderNotSignedIn, match="hermes auth add openai-codex"):
        _plugin(declares=True).mint(allowed.url)
    assert allowed.seen == []


def _allow(monkeypatch, *stubs_):
    origins, resolve = ppr._PROVIDERS["openai-codex"]
    extra = {("http", "127.0.0.1", s.server.server_address[1]) for s in stubs_}
    monkeypatch.setitem(ppr._PROVIDERS, "openai-codex", (origins | extra, resolve))


def _pool_only_sign_in(base_url: str):
    """No singleton sign-in: the resolver falls back to a pool row routed to *base_url*."""
    (_home() / "auth.json").write_text(json.dumps({"version": 1, "credential_pool": {"openai-codex": [
        {"id": "row1", "access_token": TOKEN, "base_url": base_url}]}}))


def test_pooled_credential_goes_only_to_its_routed_origin(stubs, monkeypatch):
    routed, _ = stubs
    elsewhere = _Stub()  # allowlisted too, but not where the pooled key is routed
    _allow(monkeypatch, elsewhere)
    _pool_only_sign_in(routed.url + "/v1")
    plugin = _plugin(declares=True)
    try:
        with pytest.raises(PermissionError, match="routes"):
            plugin.mint(elsewhere.url + "/v1/realtime/client_secrets")
        assert elsewhere.seen == []
        result = plugin.mint(routed.url + "/v1/realtime/client_secrets")
    finally:
        elsewhere.close()
    assert result.status == 200
    assert routed.seen[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"


def _write_module(path: Path, manifest_dir: Path, manifest: str) -> object:
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "plugin.yaml").write_text(manifest)
    path.write_text(
        "from hermes_cli.plugin_provider_requests import credentialed_provider_request\n"
        "def mint(url):\n"
        "    return credentialed_provider_request('openai-codex', 'POST', url, json={})\n")
    spec = importlib.util.spec_from_file_location(f"plugin_mod_{abs(hash(str(path)))}", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_nested_manifest_cannot_grant_what_the_installed_manifest_does_not(stubs):
    allowed, _ = stubs
    _sign_in()
    root = _home() / "plugins" / "sneaky"
    (root).mkdir(parents=True)
    (root / "plugin.yaml").write_text("name: sneaky\n")  # the installed manifest declares nothing
    mod = _write_module(root / "dashboard" / "plugin_api.py", root / "dashboard",
                        "name: sneaky-nested\nrequires_auth: [openai-codex]\n")
    with pytest.raises(PermissionError, match="requires_auth"):
        mod.mint(allowed.url)
    assert allowed.seen == []


def test_category_plugin_is_attributed_to_its_own_manifest(stubs):
    allowed, _ = stubs
    _sign_in()
    owner = _home() / "plugins" / "voices" / "live"
    mod = _write_module(owner / "sub" / "api.py", owner, "name: live\nrequires_auth: [openai-codex]\n")
    assert mod.mint(allowed.url).status == 200
    assert allowed.seen[0]["headers"]["Authorization"] == f"Bearer {TOKEN}"


def _dangling_json(cat: Path) -> None:
    cat.mkdir(parents=True, exist_ok=True)
    (cat / "plugin.json").symlink_to(cat / "missing-target.json")


def _dir_named(name: str):
    def make(cat: Path) -> None:
        (cat / name).mkdir(parents=True, exist_ok=True)
    return make


# Layouts the verifier probed: code with its own declaring plugin.yaml in a directory discovery
# (``scan_directory``) never returns. Discovery loads nothing there, so the token must not go out.
_NOT_LOADED = {
    "dangling plugin.json symlink in category": ("cat/inner", _dangling_json),
    "plugin.json directory in category": ("cat/inner", _dir_named("plugin.json")),
    "plugin.yaml directory in category": ("cat/inner", _dir_named("plugin.yaml")),
    "dunder top-level dir": ("__hidden__", None),
    "foreign-harness top-level dir": (".claude-plugin", None),
    "foreign-harness dir in category": ("cat/.codex-plugin", None),
    "dunder dir in category": ("cat/__x__", None),
}


@pytest.mark.parametrize("rel, prepare_category", list(_NOT_LOADED.values()), ids=list(_NOT_LOADED))
def test_code_discovery_would_not_load_gets_no_token(stubs, rel, prepare_category):
    from hermes_cli.plugins_discovery import scan_directory

    allowed, _ = stubs
    _sign_in()
    plugins = _home() / "plugins"
    owner = plugins / rel
    if prepare_category is not None:
        prepare_category(owner.parent)
    mod = _write_module(owner / "api.py", owner, "name: probe\nrequires_auth: [openai-codex]\n")
    assert not any(Path(m.path) == owner for m in scan_directory(plugins, "user"))  # discovery agrees
    with pytest.raises(PermissionError, match="installed plugin"):
        mod.mint(allowed.url)
    assert allowed.seen == []
