# Attribution headers for the switchyard router — unit tests.
#
# Contract under test (agent/switchyard_attribution.py, 2026-08-17):
#   * only loopback base URLs get headers
#   * aux tasks are labeled aux:{job}; main loop is "main"
#   * ambient session comes from aux_accounting context, "standalone" otherwise
#   * caller headers win over ambient attribution; merges never drop keys

import pytest

from agent.switchyard_attribution import (
    TASK_HEADER,
    SESSION_ID_HEADER,
    TRIAL_ID_HEADER,
    apply_to_request_kwargs,
    aux_task_label,
    build_attribution_headers,
    is_switchyard_base_url,
    merge_attribution_headers,
)


class TestBaseUrlGate:
    def test_loopback_ipv4(self):
        assert is_switchyard_base_url("http://127.0.0.1:4000/v1")

    def test_loopback_localhost(self):
        assert is_switchyard_base_url("http://localhost:4000/v1")

    def test_loopback_ipv6(self):
        assert is_switchyard_base_url("http://[::1]:4000/v1")

    def test_lan_host_rejected(self):
        assert not is_switchyard_base_url("http://192.168.1.238:1234/v1")

    def test_public_api_rejected(self):
        for url in (
            "https://api.anthropic.com",
            "https://api.openai.com/v1",
            "https://openrouter.ai/api/v1",
        ):
            assert not is_switchyard_base_url(url)

    def test_empty_and_garbage(self):
        for url in ("", None, "not a url", "::::"):
            assert not is_switchyard_base_url(url)


class TestBuildHeaders:
    def test_aux_task_label(self):
        assert aux_task_label("vision") == "aux:vision"
        assert aux_task_label("compression") == "aux:compression"
        assert aux_task_label(None) == "aux:unknown"
        # already-prefixed stays
        assert aux_task_label("aux:vision") == "aux:vision"

    def test_explicit_session_and_turn(self):
        headers = build_attribution_headers(
            "http://127.0.0.1:4000/v1",
            task="aux:vision",
            session_id="20260817_120000_ab",
            turn_id="turn-7",
        )
        assert headers[SESSION_ID_HEADER] == "20260817_120000_ab"
        assert headers[TASK_HEADER] == "aux:vision"
        assert headers[TRIAL_ID_HEADER] == "turn-7"

    def test_ambient_session_from_accounting_context(self):
        from agent import aux_accounting

        token = aux_accounting.set_accounting_context(object(), "ambient-sess")
        try:
            headers = build_attribution_headers(
                "http://127.0.0.1:4000/v1", task="aux:vision"
            )
        finally:
            aux_accounting.reset_accounting_context(token)
        assert headers[SESSION_ID_HEADER] == "ambient-sess"

    def test_standalone_when_no_context(self):
        from agent import aux_accounting

        token = aux_accounting.set_accounting_context(None, None)
        try:
            headers = build_attribution_headers(
                "http://127.0.0.1:4000/v1", task="aux:vision"
            )
        finally:
            aux_accounting.reset_accounting_context(token)
        assert headers[SESSION_ID_HEADER] == "standalone"

    def test_no_headers_for_public_url(self):
        assert (
            build_attribution_headers(
                "https://api.anthropic.com", task="aux:vision", session_id="s"
            )
            == {}
        )


class TestMerge:
    def test_caller_wins_on_collision(self):
        merged = merge_attribution_headers(
            {SESSION_ID_HEADER: "explicit"}, {SESSION_ID_HEADER: "ambient"}
        )
        assert merged[SESSION_ID_HEADER] == "explicit"

    def test_attribution_added_when_absent(self):
        merged = merge_attribution_headers(
            {"x-initiator": "user"},
            {SESSION_ID_HEADER: "s", TASK_HEADER: "aux:vision"},
        )
        assert merged["x-initiator"] == "user"
        assert merged[SESSION_ID_HEADER] == "s"
        assert merged[TASK_HEADER] == "aux:vision"

    def test_empty_attribution_returns_copy_or_none(self):
        assert merge_attribution_headers({"a": "1"}, {}) == {"a": "1"}
        assert merge_attribution_headers(None, {}) is None

    def test_merge_returns_fresh_dict(self):
        original = {"a": "1"}
        merged = merge_attribution_headers(original, {SESSION_ID_HEADER: "s"})
        assert merged is not original
        assert original == {"a": "1"}


class TestApplyToKwargs:
    def test_aux_kwargs_gain_headers(self):
        kwargs = {"model": "vision-aux", "messages": []}
        apply_to_request_kwargs(
            kwargs,
            base_url="http://127.0.0.1:4000/v1",
            task="vision",
            session_id="sess-1",
        )
        assert kwargs["extra_headers"][TASK_HEADER] == "aux:vision"
        assert kwargs["extra_headers"][SESSION_ID_HEADER] == "sess-1"

    def test_main_kwargs_gain_headers(self):
        kwargs = {"model": "hermes", "messages": []}
        apply_to_request_kwargs(
            kwargs,
            base_url="http://localhost:4000/v1",
            task="main",
            session_id="sess-1",
            turn_id="t1",
        )
        assert kwargs["extra_headers"][TASK_HEADER] == "main"
        assert kwargs["extra_headers"][TRIAL_ID_HEADER] == "t1"

    def test_public_target_untouched(self):
        kwargs = {"model": "claude-fable-5", "messages": []}
        apply_to_request_kwargs(
            kwargs,
            base_url="https://api.anthropic.com",
            task="main",
            session_id="s",
        )
        assert "extra_headers" not in kwargs

    def test_existing_headers_preserved(self):
        kwargs = {
            "model": "hermes-air",
            "messages": [],
            "extra_headers": {"x-initiator": "user"},
        }
        apply_to_request_kwargs(
            kwargs,
            base_url="http://127.0.0.1:4000/v1",
            task="main",
            session_id="sess-1",
        )
        eh = kwargs["extra_headers"]
        assert eh["x-initiator"] == "user"
        assert eh[SESSION_ID_HEADER] == "sess-1"

    def test_none_task_treated_as_main(self):
        kwargs = {"model": "hermes", "messages": []}
        apply_to_request_kwargs(
            kwargs, base_url="http://127.0.0.1:4000/v1", session_id="s"
        )
        assert kwargs["extra_headers"][TASK_HEADER] == "main"
