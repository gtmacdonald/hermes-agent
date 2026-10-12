"""Anthropic OAuth refresh for :class:`agent.credential_pool.CredentialPool`.

POST the refresh, then commit the rotated pair to its authoritative singleton or fail closed.
Facade names are late-imported so patches on ``agent.credential_pool`` stay the seam.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.credential_pool import PooledCredential


class CredentialPoolAnthropicRefreshMixin:
    def _commit_anthropic_rotation(
        self, entry: PooledCredential, refreshed: dict[str, Any]
    ) -> None:
        """Write a rotated Anthropic pair to its authoritative singleton, or fail closed.

        claude_code -> ~/.claude/.credentials.json (so the fallback resolver
        and other profiles see it). hermes_pkce -> ~/.hermes/.anthropic_oauth.json
        (``_seed_from_singletons`` re-seeds it every load; a borrowed row commits
        to the ROOT's file, never a new profile-local copy, #100339). Not
        ``endswith``: manual:hermes_pkce is pool-owned and a singleton for it
        would be a second authority for the same refresh-token family.
        """
        if entry.source == "claude_code":
            store = "~/.claude/.credentials.json"
        elif entry.source == "hermes_pkce":
            store = "~/.hermes/.anthropic_oauth.json"
        else:
            return
        from agent.credential_pool import _RefreshDone, _singleton_target_for_entry
        try:
            from agent import anthropic_credentials as ac
            args = (refreshed["access_token"], refreshed["refresh_token"], refreshed["expires_at_ms"])
            if entry.source == "claude_code":
                ac._write_claude_code_credentials(*args, spent_refresh_token=entry.refresh_token or "")
            else:
                ac._write_hermes_oauth_credentials(*args, target=_singleton_target_for_entry(self, entry))
        except Exception as wexc:  # health: allow BLE001 -- moved verbatim from credential_pool; any singleton write failure must fail the rotation closed
            # Authoritative commit failed: do not mark, persist or return the
            # rotation as successful, and bypass the re-POST recovery path —
            # there is nothing left to retry with.
            raise _RefreshDone(self._fail_closed_unpersisted_rotation(entry, wexc, store=store))

    def _refresh_anthropic(self, entry: PooledCredential) -> PooledCredential:
        """POST the Anthropic refresh, commit to the singleton, return the rotated (unpersisted) entry."""
        from agent.credential_pool import _RefreshDone
        from agent.anthropic_credentials import (
            is_rotation_consumed_uncommitted,
            refresh_anthropic_oauth_pure,
            spent_rotation_source_path,
        )

        # Never POST a refresh token another process already spent: the
        # durable sidecar verdict is what a fresh interpreter sees here.
        source_path = spent_rotation_source_path(entry.source)
        if is_rotation_consumed_uncommitted(entry.refresh_token, source_path=source_path) or (
            is_rotation_consumed_uncommitted(entry.access_token, source_path=source_path)
        ):
            raise _RefreshDone(self._fail_closed_unpersisted_rotation(
                entry,
                RuntimeError(
                    "credential pair was rotated by another process but the "
                    "rotation never committed (spent-rotation sidecar verdict)"
                ),
                store=str(source_path or "credential store"),
            ))
        refreshed = refresh_anthropic_oauth_pure(entry.refresh_token, use_json=entry.source.endswith("hermes_pkce"))
        updated = replace(
            entry,
            access_token=refreshed["access_token"],
            refresh_token=refreshed["refresh_token"],
            expires_at_ms=refreshed["expires_at_ms"],
        )
        from agent.oauth_account_fallback import rebind_identity
        updated = rebind_identity(entry, updated)
        self._commit_anthropic_rotation(entry, refreshed)
        return updated
