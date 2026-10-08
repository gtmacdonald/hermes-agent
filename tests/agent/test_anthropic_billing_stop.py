"""Fork-local spending policy: a depleted Anthropic Platform balance ends the turn.

Neither the main loop (``anthropic_billing_stop``) nor the auxiliary ladder may rotate
credentials or spill over to a metered fallback when native Anthropic reports billing.
"""
from types import SimpleNamespace

import pytest

import agent.auxiliary_client as aux
from agent.anthropic_endpoints import is_native_anthropic_platform
from agent.error_classifier import FailoverReason
from agent.turn_api_error import ANTHROPIC_BILLING_STOP_MESSAGE, anthropic_billing_stop

_BILLING = SimpleNamespace(reason=FailoverReason.billing)


@pytest.mark.parametrize("provider, base_url, expected", [
    ("anthropic", None, True),
    ("custom", "https://api.anthropic.com/v1", True),
    ("custom", "https://api.anthropic.com.evil.example/v1", False),
    ("openrouter", "https://openrouter.ai/api/v1", False),
    ("minimax", "https://api.minimax.io/anthropic", False),
])
def test_native_anthropic_platform_detection(provider, base_url, expected):
    assert is_native_anthropic_platform(provider, base_url) is expected


def test_billing_on_native_anthropic_stops_the_turn_without_retry():
    result = anthropic_billing_stop(_BILLING, "anthropic", "", messages=["m"], api_call_count=3)
    assert result["failed"] and not result["completed"]
    assert result["failure_reason"] == "billing" and result["failure_retryable"] is False
    assert result["subscription_handoff_required"] is True
    assert result["final_response"] == result["error"] == ANTHROPIC_BILLING_STOP_MESSAGE
    assert result["messages"] == ["m"] and result["api_calls"] == 3


@pytest.mark.parametrize("reason, provider", [
    (FailoverReason.billing, "openrouter"),
    (FailoverReason.rate_limit, "anthropic"),
])
def test_other_providers_and_reasons_keep_normal_recovery(reason, provider):
    classified = SimpleNamespace(reason=reason)
    assert anthropic_billing_stop(classified, provider, "", messages=[], api_call_count=0) is None


class _CreditError(Exception):
    status_code = 402


def _route(provider, base_info):
    return aux._LadderRoute(
        client=None, task=None, tag="test", async_mode=False, base_info=base_info,
        resolved_provider=provider, resolved_model="m", resolved_base_url=base_info,
        resolved_api_key=None, resolved_api_mode=None, final_model="m", main_runtime=None,
        route_info=None, timeout=None)


def test_aux_ladder_refuses_metered_spillover_from_native_anthropic():
    ladder = aux._ladder_provider_fallback(_CreditError("credit balance is too low"),
                                           _route("anthropic", "https://api.anthropic.com"))
    with pytest.raises(RuntimeError, match="metered gateway spillover is disabled"):
        next(ladder)
