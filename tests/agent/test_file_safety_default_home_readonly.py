"""Tests for the default/home readonly-by-default write posture.

Ruling: the default profile's home (the Hermes root, ``~/.hermes``) and the
per-task sandbox-mirror homes created by non-local terminal backends are
READONLY by default. Reads are unaffected; writes are denied unless the
operator opts in via ``HERMES_ALLOW_WRITE_DEFAULT_HOME=1`` or
``file_safety.allow_write_default_home: true``.

Non-default profile homes (``<root>/profiles/<name>/...``) keep their existing
posture — only the *default* home flips.
"""
from __future__ import annotations

import os

import pytest

import agent.file_safety as fs


@pytest.fixture(autouse=True)
def _no_safe_root(monkeypatch):
    monkeypatch.delenv("HERMES_WRITE_SAFE_ROOT", raising=False)
    monkeypatch.delenv(fs.ALLOW_WRITE_DEFAULT_HOME_ENV, raising=False)
    # Never let a real ~/.hermes/config.yaml opt the test host in.
    monkeypatch.setattr(fs, "default_home_writes_allowed", fs.default_home_writes_allowed)
    yield


@pytest.fixture
def hermes_root(tmp_path, monkeypatch):
    """A fake Hermes root standing in for ``~/.hermes`` (the default home)."""
    root = tmp_path / ".hermes"
    (root / "profiles" / "studio").mkdir(parents=True)
    (root / "memories").mkdir()
    (root / "kanban").mkdir()
    monkeypatch.setattr(fs, "_hermes_root_path", lambda: root)
    monkeypatch.setattr(fs, "_hermes_home_path", lambda: root / "profiles" / "studio")
    # No config file on the fake root -> opt-in resolves False.
    monkeypatch.setattr(fs, "default_home_writes_allowed", lambda: False)
    return root


# ---------------------------------------------------------------------------
# classify_default_home_write
# ---------------------------------------------------------------------------


class TestClassifyDefaultHomeWrite:
    def test_root_itself_classified(self, hermes_root):
        info = fs.classify_default_home_write(str(hermes_root))
        assert info is not None
        assert info["kind"] == "default_home"

    @pytest.mark.parametrize(
        "rel",
        ["SOUL.md", "config.yaml", "memories/MEMORY.md", "skills/foo/SKILL.md"],
    )
    def test_default_home_state_classified(self, hermes_root, rel):
        info = fs.classify_default_home_write(str(hermes_root / rel))
        assert info is not None
        assert info["kind"] == "default_home"
        assert info["home_root"] == os.path.realpath(hermes_root)

    @pytest.mark.parametrize(
        "rel",
        [
            "profiles/studio/SOUL.md",
            "profiles/other/skills/x/SKILL.md",
            "kanban/boards/syndicate/workspaces/t_1/out.txt",
        ],
    )
    def test_shared_and_profile_areas_not_classified(self, hermes_root, rel):
        assert fs.classify_default_home_write(str(hermes_root / rel)) is None

    def test_outside_hermes_not_classified(self, hermes_root, tmp_path):
        assert fs.classify_default_home_write(str(tmp_path / "project" / "a.py")) is None

    def test_sandbox_mirror_home_classified(self, hermes_root, tmp_path):
        target = (
            tmp_path
            / "profiles" / "group1"
            / "sandboxes" / "docker" / "default" / "home" / ".hermes"
            / "profiles" / "group1" / "SOUL.md"
        )
        info = fs.classify_default_home_write(str(target))
        assert info is not None
        assert info["kind"] == "sandbox_mirror_home"
        assert info["home_root"].endswith(
            os.path.join("sandboxes", "docker", "default", "home")
        )

    def test_sandbox_mirror_non_hermes_file_still_classified(self, hermes_root, tmp_path):
        """The whole mirrored home is readonly, not just its .hermes subtree."""
        target = tmp_path / "sandboxes" / "docker" / "t1" / "home" / "scratch.txt"
        info = fs.classify_default_home_write(str(target))
        assert info is not None
        assert info["kind"] == "sandbox_mirror_home"


# ---------------------------------------------------------------------------
# Denial wiring
# ---------------------------------------------------------------------------


class TestWriteDenialPosture:
    def test_default_home_write_denied(self, hermes_root):
        target = str(hermes_root / "SOUL.md")
        assert fs.is_write_denied(target) is True
        err = fs.get_write_denied_error(target)
        assert err is not None
        assert "readonly by default" in err
        assert fs.ALLOW_WRITE_DEFAULT_HOME_ENV in err
        assert "allow_write_default_home" in err

    def test_sandbox_mirror_home_write_denied(self, hermes_root, tmp_path):
        target = str(
            tmp_path / "sandboxes" / "docker" / "default" / "home" / ".hermes" / "SOUL.md"
        )
        err = fs.get_write_denied_error(target)
        assert err is not None
        assert "sandbox-mirror home" in err

    def test_verb_is_respected(self, hermes_root):
        err = fs.get_write_denied_error(str(hermes_root / "SOUL.md"), verb="Delete")
        assert err.startswith("Delete denied:")

    def test_non_default_profile_home_still_writable(self, hermes_root):
        target = str(hermes_root / "profiles" / "studio" / "SOUL.md")
        assert fs.is_write_denied(target) is False

    def test_kanban_workspace_still_writable(self, hermes_root):
        target = str(hermes_root / "kanban" / "boards" / "b" / "workspaces" / "t" / "x.md")
        assert fs.is_write_denied(target) is False

    def test_ordinary_project_path_unaffected(self, hermes_root, tmp_path):
        assert fs.is_write_denied(str(tmp_path / "proj" / "main.py")) is False

    def test_credential_reason_wins_over_default_home(self, hermes_root, monkeypatch):
        """A credential inside the default home reports the credential reason."""
        target = str(hermes_root / ".env")
        err = fs.get_write_denied_error(target)
        assert err is not None
        assert "protected system/credential file" in err

    def test_reads_are_unaffected(self, hermes_root):
        """Readonly means readonly — the read-deny list must not grow."""
        assert fs.get_read_block_error(str(hermes_root / "SOUL.md")) is None
        assert fs.get_read_block_error(str(hermes_root / "memories" / "MEMORY.md")) is None


# ---------------------------------------------------------------------------
# Opt-in
# ---------------------------------------------------------------------------


class TestOptIn:
    def test_env_opt_in_restores_write(self, hermes_root, monkeypatch):
        target = str(hermes_root / "SOUL.md")
        assert fs.is_write_denied(target) is True
        monkeypatch.setattr(fs, "default_home_writes_allowed", lambda: True)
        assert fs.is_write_denied(target) is False
        assert fs.get_write_denied_error(target) is None

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
    def test_env_var_truthy_values(self, monkeypatch, value):
        monkeypatch.setenv(fs.ALLOW_WRITE_DEFAULT_HOME_ENV, value)
        assert fs.default_home_writes_allowed() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", ""])
    def test_env_var_falsy_values_fall_through_to_config(self, monkeypatch, value):
        monkeypatch.setenv(fs.ALLOW_WRITE_DEFAULT_HOME_ENV, value)
        # Config lookup is stubbed to a config without the flag.
        import hermes_cli.config as cfg_mod
        monkeypatch.setattr(cfg_mod, "load_config_readonly", lambda: {})
        assert fs.default_home_writes_allowed() is False

    def test_config_opt_in(self, monkeypatch):
        import hermes_cli.config as cfg_mod
        monkeypatch.setattr(
            cfg_mod,
            "load_config_readonly",
            lambda: {"file_safety": {"allow_write_default_home": True}},
        )
        assert fs.default_home_writes_allowed() is True

    def test_config_absent_fails_closed(self, monkeypatch):
        import hermes_cli.config as cfg_mod

        def boom():
            raise RuntimeError("no config")

        monkeypatch.setattr(cfg_mod, "load_config_readonly", boom)
        assert fs.default_home_writes_allowed() is False
