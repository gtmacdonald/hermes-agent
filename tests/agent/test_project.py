"""Tests for the /project and /topic-set slash commands and their helper module.

Covers the shared ``agent.project`` helper (cwd change, note generation) and
the command registry entries that make ``/project`` and ``/topic-set``
discoverable across all surfaces (CLI, TUI/desktop, gateway).
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.project import (
    PENDING_NOTE_ATTR,
    PENDING_TOPIC_ATTR,
    apply_project_change,
    build_project_note,
    get_project_topic,
    take_project_note,
)
from hermes_cli.commands import COMMAND_REGISTRY, GATEWAY_KNOWN_COMMANDS, resolve_command


# ---------------------------------------------------------------------------
# Command registry tests
# ---------------------------------------------------------------------------


class TestProjectCommandRegistry:
    def test_project_registered(self):
        cmd = resolve_command("project")
        assert cmd is not None
        assert cmd.name == "project"
        assert not cmd.gateway_only
        assert not cmd.cli_only
        assert cmd.busy_policy == "dispatch"
        assert "project" in GATEWAY_KNOWN_COMMANDS

    def test_cd_alias_resolves_to_project(self):
        cmd = resolve_command("cd")
        assert cmd is not None
        assert cmd.name == "project"

    def test_topic_set_registered(self):
        cmd = resolve_command("topic-set")
        assert cmd is not None
        assert cmd.name == "topic-set"
        assert not cmd.gateway_only
        assert not cmd.cli_only
        assert cmd.busy_policy == "dispatch"
        assert "topic-set" in GATEWAY_KNOWN_COMMANDS

    def test_project_and_topic_are_distinct_from_gateway_topic(self):
        """The gateway-only /topic (Telegram DM topics) must remain separate."""
        topic_cmd = resolve_command("topic")
        assert topic_cmd is not None
        assert topic_cmd.gateway_only is True
        assert topic_cmd.name == "topic"

        project_cmd = resolve_command("project")
        assert project_cmd is not None
        assert project_cmd.gateway_only is False


# ---------------------------------------------------------------------------
# build_project_note tests
# ---------------------------------------------------------------------------


class TestBuildProjectNote:
    def test_note_includes_cwd_change(self):
        note = build_project_note(
            old_cwd="/old/path",
            new_cwd="/new/path",
            old_topic=None,
            new_topic=None,
        )
        assert "Project context changed:" in note
        assert "/old/path" in note
        assert "/new/path" in note
        assert "working directory changed" in note

    def test_note_includes_topic_change(self):
        note = build_project_note(
            old_cwd="/same/path",
            new_cwd="/same/path",
            old_topic=None,
            new_topic="auth refactor",
        )
        assert "Topic label set to" in note
        assert "auth refactor" in note
        # No cwd change when old == new
        assert "working directory changed" not in note

    def test_note_includes_both_cwd_and_topic_change(self):
        note = build_project_note(
            old_cwd="/old",
            new_cwd="/new",
            old_topic="old topic",
            new_topic="new topic",
        )
        assert "working directory changed" in note
        assert "Topic label set to" in note
        assert "new topic" in note

    def test_note_includes_topic_clear(self):
        note = build_project_note(
            old_cwd="/same",
            new_cwd="/same",
            old_topic="old topic",
            new_topic=None,
        )
        assert "Topic label cleared" in note

    def test_note_is_wrapped_in_brackets(self):
        note = build_project_note(
            old_cwd="/old",
            new_cwd="/new",
            old_topic=None,
            new_topic=None,
        )
        assert note.startswith("[")
        assert note.endswith("]")


# ---------------------------------------------------------------------------
# apply_project_change tests (CLI/local backend — os.chdir + TERMINAL_CWD)
# ---------------------------------------------------------------------------


class TestApplyProjectChange:
    def test_changes_cwd_and_returns_note(self, tmp_path):
        original_cwd = os.getcwd()
        try:
            note = apply_project_change(str(tmp_path), session_key="test-session")
            assert os.getcwd() == str(tmp_path)
            assert os.environ.get("TERMINAL_CWD") == str(tmp_path)
            assert "Project context changed:" in note
            assert str(tmp_path) in note
        finally:
            os.chdir(original_cwd)
            os.environ.pop("TERMINAL_CWD", None)

    def test_sets_topic_attr_on_cli_instance(self, tmp_path):
        original_cwd = os.getcwd()
        try:
            class FakeCLI:
                pass

            cli = FakeCLI()
            note = apply_project_change(
                str(tmp_path),
                topic="my topic",
                old_topic=None,
                session_key="test",
            )
            setattr(cli, PENDING_TOPIC_ATTR, "my topic")
            setattr(cli, PENDING_NOTE_ATTR, note)
            assert get_project_topic(cli) == "my topic"
            assert take_project_note(cli) == note
            # Note is consumed (one-shot)
            assert take_project_note(cli) == ""
        finally:
            os.chdir(original_cwd)
            os.environ.pop("TERMINAL_CWD", None)

    def test_nonexistent_directory_raises(self):
        with pytest.raises(ValueError, match="does not exist"):
            apply_project_change("/nonexistent/path/that/should/not/exist")

    def test_tilde_expansion(self, tmp_path, monkeypatch):
        # Set HOME to a tmp dir so ~ expands there
        monkeypatch.setenv("HOME", str(tmp_path))
        target = tmp_path / "project"
        target.mkdir()
        original_cwd = os.getcwd()
        try:
            note = apply_project_change("~/project", session_key="test")
            assert os.getcwd() == str(target)
            assert str(target) in note
        finally:
            os.chdir(original_cwd)
            os.environ.pop("TERMINAL_CWD", None)


# ---------------------------------------------------------------------------
# take_project_note + get_project_topic tests
# ---------------------------------------------------------------------------


class TestNoteStorage:
    def test_take_note_from_dict(self):
        d = {}
        d[PENDING_NOTE_ATTR] = "test note"
        assert take_project_note(d) == "test note"
        assert take_project_note(d) == ""

    def test_take_note_from_object(self):
        class Obj:
            pass

        o = Obj()
        setattr(o, PENDING_NOTE_ATTR, "test note")
        assert take_project_note(o) == "test note"
        assert take_project_note(o) == ""

    def test_take_note_when_empty(self):
        d = {}
        assert take_project_note(d) == ""

    def test_get_topic_from_dict(self):
        d = {PENDING_TOPIC_ATTR: "my topic"}
        assert get_project_topic(d) == "my topic"

    def test_get_topic_from_object(self):
        class Obj:
            pass

        o = Obj()
        setattr(o, PENDING_TOPIC_ATTR, "topic label")
        assert get_project_topic(o) == "topic label"

    def test_get_topic_when_unset(self):
        d = {}
        assert get_project_topic(d) == ""
