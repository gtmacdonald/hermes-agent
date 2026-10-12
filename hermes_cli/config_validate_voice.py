"""``voice`` section validation for ``hermes config check``.

Split out of ``hermes_cli/config.py`` (code-health size ratchet); late-imports the facade's issue helper.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hermes_cli.config import ConfigIssue


def validate_voice(config: dict[str, Any], issues: list[ConfigIssue]) -> None:
    from hermes_cli.config import _issue
    voice_cfg = config.get("voice")
    if not (isinstance(voice_cfg, dict) and "submit_mode" in voice_cfg):
        return
    submit_mode = voice_cfg.get("submit_mode")
    normalized = submit_mode.strip().lower() if isinstance(submit_mode, str) else None
    if normalized not in {"direct", "draft"}:
        _issue(issues, "error", f"voice.submit_mode must be 'direct' or 'draft', got {submit_mode!r}",
               "Set voice.submit_mode to direct (submit immediately) or draft (edit before sending)")
