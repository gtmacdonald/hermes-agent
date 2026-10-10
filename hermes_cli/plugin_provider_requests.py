"""Signed-in provider requests that core makes FOR plugins; the token never reaches plugin code.

A plugin that needs the user's subscription sign-in (today: Codex / ChatGPT) must not read, refresh
or rewrite Hermes's auth store or ``~/.codex/auth.json``. Codex refresh tokens are single-use, so a
second refresher races Hermes's locked refresh and can sign the user out. Instead the plugin declares
``requires_auth: [openai-codex]`` in its ``plugin.yaml`` and calls :func:`credentialed_provider_request`.
Hermes resolves the credential through its own locked-refresh path, attaches ``Authorization``
itself, sends only to that provider's origins, never follows a redirect, and hands back
status, headers and body.

The caller is identified by file location: the nearest stack frame under a plugins directory,
attributed to the plugin directory discovery itself returns for that file. Like capabilities this is consent and visibility, not a sandbox. In-process plugin code
can still read any file the user can.
"""

from __future__ import annotations

import json as _json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from hermes_cli.urllib_security import url_origin


class ProviderNotSignedIn(RuntimeError):
    """The user has no usable sign-in for the provider in this Hermes profile."""


@dataclass(frozen=True)
class ProviderResponse:
    status: int
    headers: dict[str, str]
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self) -> Any:
        return _json.loads(self.body)


def _codex_auth(url: str, origins: frozenset) -> tuple[dict[str, str], frozenset]:
    """``(auth headers, origins this credential may be sent to)`` from Hermes's locked-refresh resolver.

    Only the profile's own ChatGPT sign-in (the Hermes auth store, routed to the canonical Codex
    backend) may cross between the provider's origins: core's Codex chat backend is chatgpt.com
    (``DEFAULT_CODEX_BASE_URL``), and that OAuth access token is what hermes-live-voice uses to mint
    an OpenAI Realtime client secret at api.openai.com (commit 4070f011a2; the
    ``website/docs/developer-guide/plugins`` example). Every other credential is pinned to the
    origin of its routed base URL: a pooled row (gateway key, api.openai.com key, or OAuth row) and
    a sign-in rerouted by ``HERMES_CODEX_BASE_URL`` belong to that host only (#121486)."""
    from agent.codex_headers import codex_account_headers
    from agent.turn_failure_copy import oauth_relogin_command
    from hermes_cli.auth_codex import resolve_codex_runtime_credentials
    from hermes_cli.auth_constants import DEFAULT_CODEX_BASE_URL, AuthError

    try:
        creds = resolve_codex_runtime_credentials()
    except AuthError as exc:
        if not exc.relogin_required:
            raise
        creds = {}
    token = str(creds.get("api_key") or "").strip()
    if not token:
        raise ProviderNotSignedIn(
            f"Not signed in to Codex in Hermes. Run `{oauth_relogin_command('openai-codex')}` "
            "or sign in to OpenAI Codex under Providers in the Hermes desktop app.")
    headers = codex_account_headers(token) if url_origin(url)[1] == "chatgpt.com" else {}
    headers["Authorization"] = f"Bearer {token}"
    route = url_origin(str(creds.get("base_url") or ""))
    if route not in origins:
        return headers, frozenset()  # a custom endpoint's key: none of the provider's origins
    own_sign_in = creds.get("source") != "credential_pool" and route == url_origin(DEFAULT_CODEX_BASE_URL)
    return headers, origins if own_sign_in else frozenset({route})


# provider -> (origins its token may be sent to, auth resolver). chatgpt.com is the Codex backend
# core itself calls; api.openai.com hosts the Realtime client-secret endpoint Codex clients use.
# The resolver narrows the origins to the ones the resolved credential itself may reach.
_PROVIDERS: dict[str, tuple[frozenset, Callable[[str, frozenset], tuple[dict[str, str], frozenset]]]] = {
    "openai-codex": (frozenset({("https", "chatgpt.com", 443), ("https", "api.openai.com", 443)}), _codex_auth),
}


def _calling_plugin_dir() -> Optional[Path]:
    """Directory of the installed plugin whose code is nearest on the calling stack, or None.

    The nearest frame under a plugins root decides: when no discovered plugin owns it, the call is
    refused rather than attributed to a plugin further up the stack."""
    from hermes_cli.plugins import get_bundled_plugins_dir
    from hermes_constants import get_default_hermes_root, get_hermes_home, get_process_hermes_home

    bases = (get_hermes_home() / "plugins", get_process_hermes_home() / "plugins",
             get_default_hermes_root() / "plugins", get_bundled_plugins_dir())
    roots = {r for base in bases for r in (base.absolute(), base.resolve())}
    frame = sys._getframe(2)
    while frame is not None:
        raw = Path(frame.f_code.co_filename)
        in_root = False
        for path in (raw.absolute(), raw.resolve()):
            for root in (r for r in roots if path.is_relative_to(r) and path != r):
                in_root = True
                owner = _owning_plugin_dir(root, path)
                if owner is not None:
                    return owner
        if in_root:
            return None
        frame = frame.f_back
    return None


def _owning_plugin_dir(root: Path, path: Path) -> Optional[Path]:
    """The plugin directory under *root* that owns *path*: one of the directories discovery's own
    ``scan_directory(root)`` returns, so whatever discovery skips gets no credential either (dunder
    and foreign-harness directories, and a directory whose manifest entry is present but unusable,
    such as a dangling ``plugin.json`` symlink, which also stops discovery from descending into it).
    Discovery never descends into a directory it returned, so at most one contains *path*, and a
    ``plugin.yaml`` nested inside a plugin never stands in for the installed one. A portable
    ``plugin.json`` package owns its tree but has no native manifest, so it declares no
    ``requires_auth`` and is refused."""
    from hermes_cli.plugins_discovery import scan_directory

    return next((Path(m.path) for m in scan_directory(root, "user")
                 if m.path and path.is_relative_to(Path(m.path))), None)


def declared_auth_providers(manifest: Mapping[str, Any]) -> list[str]:
    """``requires_auth`` from a manifest dict: the providers whose sign-in the plugin uses."""
    raw = (manifest or {}).get("requires_auth")
    return [str(p).strip() for p in raw if str(p).strip()] if isinstance(raw, list) else []


def requires_auth_notice(manifest: Mapping[str, Any]) -> str:
    """One line for install/show surfaces; empty when the plugin declares no sign-in use."""
    providers = declared_auth_providers(manifest)
    return (f"Uses your sign-in for: {', '.join(providers)} (Hermes sends the requests; "
            "the plugin never receives the token)") if providers else ""


def _declared_providers(plugin_dir: Path) -> list[str]:
    from pm.plugin_declarations import native_manifest_file, read_native_manifest

    manifest = native_manifest_file(plugin_dir)
    return declared_auth_providers(read_native_manifest(manifest)) if manifest else []


def credentialed_provider_request(
    provider: str, method: str, url: str, *, json: Any = None,
    headers: Optional[Mapping[str, str]] = None, timeout: float = 30.0,
) -> ProviderResponse:
    """Send ``method url`` with the user's *provider* sign-in attached by Hermes.

    Refuses (``PermissionError``) a caller outside an installed plugin, a plugin whose ``plugin.yaml``
    does not list *provider* under ``requires_auth``, and any origin outside the provider's
    allowlist; raises :class:`ProviderNotSignedIn` when this profile has no sign-in. Redirects are
    returned as-is, never followed. The result carries no credential.
    """
    spec = _PROVIDERS.get(provider)
    if spec is None:
        raise ValueError(f"No signed-in requests for provider {provider!r} (supported: {', '.join(_PROVIDERS)})")
    origins, resolve = spec
    plugin_dir = _calling_plugin_dir()
    if plugin_dir is None:
        raise PermissionError("credentialed_provider_request must be called from an installed plugin's code")
    if provider not in _declared_providers(plugin_dir):
        raise PermissionError(
            f"Plugin '{plugin_dir.name}' must declare `requires_auth: [{provider}]` in plugin.yaml")
    if url_origin(url) not in origins:
        raise PermissionError(f"Refusing to send the {provider} sign-in to {url_origin(url)[1] or url!r}")
    auth_headers, credential_origins = resolve(url, origins)
    if url_origin(url) not in credential_origins:
        # A pooled or rerouted key belongs to its routed host only (#121486).
        raise PermissionError(
            f"This profile's {provider} credential routes to another endpoint than "
            f"{url_origin(url)[1] or url!r}; refusing")
    sent = {k: v for k, v in (headers or {}).items() if str(k).lower() not in {h.lower() for h in auth_headers}}
    import httpx

    with httpx.Client(timeout=timeout, follow_redirects=False) as client:
        response = client.request(method, url, json=json, headers={**sent, **auth_headers})
    return ProviderResponse(response.status_code, dict(response.headers), response.content)


__all__ = [
    "ProviderNotSignedIn", "ProviderResponse", "credentialed_provider_request", "declared_auth_providers",
    "requires_auth_notice",
]
