"""Tier-escalation policy."""

from __future__ import annotations

from thalyn_brain.orchestration.escalation import (
    EscalationInput,
    SandboxTier,
    resolve,
)


def _input(**kwargs: object) -> EscalationInput:
    defaults: dict[str, object] = {
        "requested_tier": SandboxTier.TIER_1,
        "description": "do something",
        "rationale": "",
        "user_override": None,
        "user_floor": None,
        "user_ceiling": None,
        "available_tiers": frozenset({SandboxTier.TIER_0, SandboxTier.TIER_1, SandboxTier.TIER_2}),
    }
    defaults.update(kwargs)
    return EscalationInput(**defaults)  # type: ignore[arg-type]


def test_no_signals_keeps_requested_tier() -> None:
    fb = resolve(_input(requested_tier=SandboxTier.TIER_1, description="rename a function"))
    assert fb.tier == SandboxTier.TIER_1
    assert fb.warning is None


def test_executes_generated_code_auto_escalates_to_tier_2() -> None:
    fb = resolve(
        _input(
            requested_tier=SandboxTier.TIER_1,
            description="Generate a Python script and execute generated code against the tests",
        )
    )
    assert fb.tier == SandboxTier.TIER_2


def test_auto_escalation_does_not_downgrade_existing_tier_2_request() -> None:
    fb = resolve(
        _input(
            requested_tier=SandboxTier.TIER_2,
            description="rename a function",
        )
    )
    assert fb.tier == SandboxTier.TIER_2


def test_user_override_wins_over_auto_escalation() -> None:
    fb = resolve(
        _input(
            requested_tier=SandboxTier.TIER_1,
            description="execute generated code",
            user_override=SandboxTier.TIER_1,
        )
    )
    # Auto-escalation would have picked Tier 2, but the user said
    # "force Tier 1." We follow the user.
    assert fb.tier == SandboxTier.TIER_1


def test_user_floor_raises_below_floor_choices() -> None:
    fb = resolve(
        _input(
            requested_tier=SandboxTier.TIER_0,
            description="read a file",
            user_floor=SandboxTier.TIER_1,
        )
    )
    assert fb.tier == SandboxTier.TIER_1


def test_user_ceiling_caps_high_choices() -> None:
    fb = resolve(
        _input(
            requested_tier=SandboxTier.TIER_2,
            description="execute generated code",
            user_ceiling=SandboxTier.TIER_1,
        )
    )
    assert fb.tier == SandboxTier.TIER_1


def test_unavailable_tier_falls_back_with_warning() -> None:
    # Available set excludes Tier 2 — falls back to Tier 1 and emits
    # a warning that names both the requested and resolved tier.
    fb = resolve(
        _input(
            requested_tier=SandboxTier.TIER_2,
            description="execute generated code",
            available_tiers=frozenset({SandboxTier.TIER_0, SandboxTier.TIER_1}),
        )
    )
    assert fb.tier == SandboxTier.TIER_1
    assert fb.warning is not None
    assert "tier_2" in fb.warning
    assert "tier_1" in fb.warning


def test_fallback_does_not_strengthen_isolation_above_request() -> None:
    """If the user asked for Tier 0 and only Tier 1+ are available,
    we fall back **down** to Tier 0 if available — we never silently
    strengthen, since stronger isolation can break tools the agent
    expected to have."""
    fb = resolve(
        _input(
            requested_tier=SandboxTier.TIER_0,
            description="read a file",
            available_tiers=frozenset({SandboxTier.TIER_0, SandboxTier.TIER_2}),
        )
    )
    assert fb.tier == SandboxTier.TIER_0


def test_to_wire_round_trip_preserves_warning() -> None:
    fb = resolve(
        _input(
            requested_tier=SandboxTier.TIER_2,
            description="execute generated code",
            available_tiers=frozenset({SandboxTier.TIER_0, SandboxTier.TIER_1}),
        )
    )
    wire = fb.to_wire()
    assert wire == {
        "tier": "tier_1",
        "requested": "tier_2",
        "warning": fb.warning,
    }


def test_parse_handles_unknown_strings() -> None:
    assert SandboxTier.parse("tier_2") == SandboxTier.TIER_2
    assert SandboxTier.parse("tier_99") is None
    assert SandboxTier.parse(None) is None
