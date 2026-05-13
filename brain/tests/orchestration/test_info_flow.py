"""Tests for the unified information-flow audit primitive."""

from __future__ import annotations

import pytest
from thalyn_brain.orchestration.info_flow import (
    DEFAULT_GATE_THRESHOLD,
    HEURISTIC_ESCALATION_THRESHOLD,
    InfoFlowAuditReport,
    InfoFlowMode,
    audit_info_flow,
    info_flow_check_log_entry,
)
from thalyn_brain.provider import AnthropicProvider

from tests.provider._fake_sdk import factory_for, result_message, text_message

# ---------------------------------------------------------------------------
# plan_vs_action mode
# ---------------------------------------------------------------------------


async def test_plan_vs_action_clean_log_scores_zero() -> None:
    plan = {
        "goal": "wire up the audit",
        "nodes": [
            {"id": "n1", "description": "draft the primitive"},
            {"id": "n2", "description": "write tests"},
        ],
    }
    log = [
        {"kind": "tool_call", "payload": {"planNodeId": "n1", "tool": "edit"}},
        {"kind": "tool_call", "payload": {"planNodeId": "n2", "tool": "pytest"}},
    ]
    report = await audit_info_flow(
        mode=InfoFlowMode.PLAN_VS_ACTION,
        source=plan,
        output="",
        context={"action_log": log},
    )
    assert report.mode is InfoFlowMode.PLAN_VS_ACTION
    assert report.drift_score == 0.0
    assert report.confidence == "high"


async def test_plan_vs_action_unmatched_node_scores_partial() -> None:
    plan = {
        "nodes": [
            {"id": "n1", "description": "draft the primitive"},
            {"id": "n2", "description": "write tests"},
        ],
    }
    log = [{"kind": "tool_call", "payload": {"planNodeId": "n1"}}]
    report = await audit_info_flow(
        mode=InfoFlowMode.PLAN_VS_ACTION,
        source=plan,
        output="",
        context={"action_log": log},
    )
    assert report.drift_score == pytest.approx(0.5)
    assert "1/2" in report.summary


# ---------------------------------------------------------------------------
# reported_vs_truth mode
# ---------------------------------------------------------------------------


async def test_reported_vs_truth_empty_report_flags_drift() -> None:
    report = await audit_info_flow(
        mode=InfoFlowMode.REPORTED_VS_TRUTH,
        source=None,
        output="   ",
    )
    assert report.drift_score == 1.0
    assert report.should_raise_gate is True
    assert "empty" in report.summary


async def test_reported_vs_truth_hedge_phrase_returns_mid_band() -> None:
    report = await audit_info_flow(
        mode=InfoFlowMode.REPORTED_VS_TRUTH,
        source=None,
        output="I'm not sure if the migration ran cleanly — would need to check.",
    )
    # Hedge is honest, not a fail. Score mid-band; confidence stays
    # medium so the renderer surfaces a pill, not a gate.
    assert 0.3 < report.drift_score < 0.7
    assert report.should_raise_gate is False


async def test_reported_vs_truth_unsupported_number_claim_scores_drift() -> None:
    source = {
        "action_log": [
            {"kind": "tool_call", "payload": {"tool": "pytest", "tests_run": 71}},
        ],
        "facts": ["all green"],
    }
    output = "Ran 99 tests; everything green."
    report = await audit_info_flow(
        mode=InfoFlowMode.REPORTED_VS_TRUTH,
        source=source,
        output=output,
    )
    # 99 isn't in the action log; the claim is unsupported.
    assert report.drift_score > 0.0
    assert "missing from source" in report.summary


async def test_reported_vs_truth_supported_claims_score_zero() -> None:
    source = {
        "action_log": [
            {"kind": "tool_call", "payload": {"tool": "pytest", "tests_run": 71}},
        ],
    }
    output = "Ran 71 tests; everything green."
    report = await audit_info_flow(
        mode=InfoFlowMode.REPORTED_VS_TRUTH,
        source=source,
        output=output,
    )
    assert report.drift_score == 0.0


# ---------------------------------------------------------------------------
# relayed_vs_source mode
# ---------------------------------------------------------------------------


async def test_relayed_vs_source_high_overlap_passes() -> None:
    source = "The migration ran cleanly; 71 tests pass; no warnings."
    relay = "The migration ran cleanly; 71 tests pass; no warnings."
    report = await audit_info_flow(
        mode=InfoFlowMode.RELAYED_VS_SOURCE,
        source=source,
        output=relay,
    )
    assert report.drift_score < HEURISTIC_ESCALATION_THRESHOLD
    assert report.confidence in {"medium", "high"}


async def test_relayed_vs_source_dropped_content_flags_drift() -> None:
    source = (
        "Migration succeeded but the rollback path raises ValueError "
        "when run twice — should fix before shipping."
    )
    relay = "Migration succeeded."
    report = await audit_info_flow(
        mode=InfoFlowMode.RELAYED_VS_SOURCE,
        source=source,
        output=relay,
    )
    # Relay drops the rollback caveat — heuristic should flag it.
    assert report.drift_score >= HEURISTIC_ESCALATION_THRESHOLD


async def test_relayed_vs_source_confidence_collapse_flags_drift() -> None:
    source = "I'm not sure the rollback handles the edge case; needs more testing."
    relay = "Rollback works fine."
    report = await audit_info_flow(
        mode=InfoFlowMode.RELAYED_VS_SOURCE,
        source=source,
        output=relay,
    )
    assert report.drift_score >= 0.6
    assert "hedging" in report.summary


# ---------------------------------------------------------------------------
# LLM critic escalation layer
# ---------------------------------------------------------------------------


async def test_llm_escalation_blends_max_with_heuristic() -> None:
    """Heuristic flags drift; the LLM agrees → confidence is high
    and the blended score is max(heuristic, llm)."""
    _fake, factory = factory_for(
        [
            text_message('{"drift_score": 0.8, "reason": "Relay loses the rollback caveat."}'),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    source = (
        "Migration succeeded but the rollback path raises ValueError "
        "when run twice — should fix before shipping."
    )
    relay = "Migration succeeded."
    report = await audit_info_flow(
        mode=InfoFlowMode.RELAYED_VS_SOURCE,
        source=source,
        output=relay,
        provider=provider,
    )
    assert report.llm_score == 0.8
    assert report.drift_score >= 0.8
    assert report.confidence == "high"
    assert "rollback caveat" in report.summary


async def test_llm_escalation_disagreement_lowers_confidence() -> None:
    """Heuristic flags drift; the LLM says it's fine → confidence is low.
    The blended (max) score still raises the gate because the worst
    signal wins, but the low confidence tells the renderer this is a
    disagreement, not a clean call."""
    _fake, factory = factory_for(
        [
            text_message('{"drift_score": 0.05, "reason": "Faithful paraphrase."}'),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    source = (
        "Migration succeeded but the rollback path raises ValueError "
        "when run twice — should fix before shipping."
    )
    relay = "Migration succeeded."
    report = await audit_info_flow(
        mode=InfoFlowMode.RELAYED_VS_SOURCE,
        source=source,
        output=relay,
        provider=provider,
    )
    assert report.confidence == "low"
    assert report.should_raise_gate is True


async def test_low_heuristic_skips_llm_call() -> None:
    """When the heuristic is well below threshold, the LLM critic is
    not invoked even if a provider is supplied. The fake SDK's
    message queue is empty — calling it would error."""
    _fake, factory = factory_for([])  # any consumption would blow up
    provider = AnthropicProvider(client_factory=factory)
    source = "The migration ran cleanly; 71 tests pass; no warnings."
    relay = "The migration ran cleanly; 71 tests pass; no warnings."
    report = await audit_info_flow(
        mode=InfoFlowMode.RELAYED_VS_SOURCE,
        source=source,
        output=relay,
        provider=provider,
    )
    assert report.llm_score is None


async def test_force_llm_runs_critic_even_below_threshold() -> None:
    _fake, factory = factory_for(
        [
            text_message('{"drift_score": 0.1, "reason": "Faithful."}'),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    report = await audit_info_flow(
        mode=InfoFlowMode.RELAYED_VS_SOURCE,
        source="foo bar baz",
        output="foo bar baz",
        provider=provider,
        force_llm=True,
    )
    assert report.llm_score == 0.1


async def test_llm_unparseable_response_keeps_heuristic_verdict() -> None:
    _fake, factory = factory_for(
        [
            text_message("not json at all"),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    source = "Migration succeeded but rollback raises."
    relay = "Migration succeeded."
    report = await audit_info_flow(
        mode=InfoFlowMode.RELAYED_VS_SOURCE,
        source=source,
        output=relay,
        provider=provider,
    )
    # No LLM verdict; heuristic carries the report. Confidence stays
    # medium since the heuristic ran alone.
    assert report.llm_score is None
    assert report.confidence == "medium"


# ---------------------------------------------------------------------------
# Wire shape + gate raising
# ---------------------------------------------------------------------------


def test_report_to_wire_includes_mode_and_refs() -> None:
    report = InfoFlowAuditReport(
        mode=InfoFlowMode.RELAYED_VS_SOURCE,
        drift_score=0.42,
        confidence="medium",
        summary="some drift",
        source_ref={"leadId": "lead_x", "turnId": "t_1"},
        output_ref={"turnId": "t_2"},
        heuristic_score=0.42,
        llm_score=None,
    )
    wire = report.to_wire()
    assert wire["mode"] == "relayed_vs_source"
    assert wire["driftScore"] == 0.42
    assert wire["confidence"] == "medium"
    assert wire["sourceRef"] == {"leadId": "lead_x", "turnId": "t_1"}
    assert wire["outputRef"] == {"turnId": "t_2"}
    assert "llmScore" not in wire


def test_should_raise_gate_high_drift() -> None:
    report = InfoFlowAuditReport(
        mode=InfoFlowMode.RELAYED_VS_SOURCE,
        drift_score=DEFAULT_GATE_THRESHOLD,
        confidence="medium",
        summary="",
    )
    assert report.should_raise_gate is True


def test_should_raise_gate_low_confidence_mid_drift() -> None:
    report = InfoFlowAuditReport(
        mode=InfoFlowMode.RELAYED_VS_SOURCE,
        drift_score=HEURISTIC_ESCALATION_THRESHOLD,
        confidence="low",
        summary="layer disagreement",
    )
    assert report.should_raise_gate is True


def test_should_raise_gate_low_drift_passes() -> None:
    report = InfoFlowAuditReport(
        mode=InfoFlowMode.RELAYED_VS_SOURCE,
        drift_score=0.1,
        confidence="high",
        summary="",
    )
    assert report.should_raise_gate is False


def test_info_flow_check_log_entry_carries_payload() -> None:
    report = InfoFlowAuditReport(
        mode=InfoFlowMode.REPORTED_VS_TRUTH,
        drift_score=0.5,
        confidence="medium",
        summary="reason here",
        source_ref={"leadId": "lead_x"},
        output_ref={"turnId": "t_99"},
    )
    entry = info_flow_check_log_entry(report)
    assert entry["kind"] == "info_flow_check"
    assert entry["payload"]["mode"] == "reported_vs_truth"
    assert entry["payload"]["summary"] == "reason here"
    assert entry["payload"]["sourceRef"] == {"leadId": "lead_x"}
