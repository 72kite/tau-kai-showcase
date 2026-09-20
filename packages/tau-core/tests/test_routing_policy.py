import pytest

from tau_core.routing import RoutingAction, ToolRoutingPolicy


def test_high_confidence_acts():
    policy = ToolRoutingPolicy(confidence_threshold=0.6)
    decision = policy.decide("get_entity_state", confidence=0.9)
    assert decision.action is RoutingAction.ACT


def test_low_confidence_clarifies():
    policy = ToolRoutingPolicy(confidence_threshold=0.6)
    decision = policy.decide("get_entity_state", confidence=0.3)
    assert decision.action is RoutingAction.CLARIFY


def test_boundary_confidence_acts():
    policy = ToolRoutingPolicy(confidence_threshold=0.6)
    decision = policy.decide("get_entity_state", confidence=0.6)
    assert decision.action is RoutingAction.ACT


def test_always_clarify_tool_overrides_high_confidence():
    policy = ToolRoutingPolicy(confidence_threshold=0.6, always_clarify_tools={"shutdown_host"})
    decision = policy.decide("shutdown_host", confidence=0.99)
    assert decision.action is RoutingAction.CLARIFY


def test_invalid_threshold_rejected():
    with pytest.raises(ValueError):
        ToolRoutingPolicy(confidence_threshold=1.5)


def test_no_router_opinion_acts_and_defers_to_the_cdg():
    """The bug this prevents, measured live on 2026-07-15: the configured router
    (qwen2.5:7b-instruct) could not produce a score at ALL - every sample raised - so every
    sample fell back to a fabricated 0.0, every call scored below threshold, and every one of
    Tau's 77 tools became NEEDS_CLARIFICATION. A total outage that no log, health check or test
    reported; the main model just looked stupid.

    None means "no opinion", which is not a judgement of "worthless". Acting on it is safe
    because routing was never the safety layer - the CDG gates the call immediately after."""
    policy = ToolRoutingPolicy(confidence_threshold=0.6)
    decision = policy.decide("get_entity_state", confidence=None)
    assert decision.action is RoutingAction.ACT
    assert "CDG" in decision.reason


def test_a_real_zero_score_still_clarifies():
    """The distinction has to cut both ways, or it is just a disabled router: a router that
    actually judged the call worthless has made a judgement, and that judgement is respected."""
    policy = ToolRoutingPolicy(confidence_threshold=0.6)
    decision = policy.decide("get_entity_state", confidence=0.0)
    assert decision.action is RoutingAction.CLARIFY


def test_always_clarify_still_wins_over_a_missing_opinion():
    """always_clarify_tools is an explicit operator instruction, not a hint - a silent router
    must not be a way around it."""
    policy = ToolRoutingPolicy(confidence_threshold=0.6, always_clarify_tools={"shutdown_host"})
    assert policy.decide("shutdown_host", confidence=None).action is RoutingAction.CLARIFY
