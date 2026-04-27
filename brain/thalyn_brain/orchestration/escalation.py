"""Tier-escalation policy for sub-agent dispatches.

The planner annotates each plan node with an optional
``sandbox_tier`` hint (``tier_0`` through ``tier_3``). The runner
asks this module to translate the hint, the node's risk signals,
and the user's per-task override into the *effective* tier the
sandbox manager should attempt — and what to do when the requested
tier is not available on the host.

Three concerns ride together:

* **Auto-escalation.** A plan node with ``executes_generated_code``
  in its description bumps to Tier 2 even if the planner asked for
  Tier 1. The bump is conservative: any signal in a small allowlist
  ("execute generated code", "untrusted dependencies", "package
  install from URL") triggers it.
* **User override.** A persisted setting (per project) can force a
  ceiling — "never run anything above Tier 2" — or a floor —
  "always at least Tier 1." The override is applied after the
  auto-escalation so the user has the final say.
* **Fallback.** When the resolved tier is not available (Tier 2 on
  Wayland without Firecracker installed, Tier 3 with no API key),
  the policy emits a `TierFallback` describing what we tried and
  what we got. The runner uses the fallback's `warning` to surface
  the degraded posture in the action log so the user sees it.

The module is deliberately stateless — every input flows in through
:class:`EscalationInput`. That keeps the policy testable and makes
it cheap to add a settings UI later.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

# Hint substrings that trigger auto-escalation to Tier 2 if the
# planner asked for Tier 1 (or didn't ask). Compared case-
# insensitively against the plan node's description + rationale.
HIGH_RISK_HINTS: frozenset[str] = frozenset(
    {
        "execute generated code",
        "run generated code",
        "compile and run",
        "untrusted dependencies",
        "untrusted source",
        "install from url",
        "curl | sh",
        "npm install",
        "pip install",
    }
)


class SandboxTier(StrEnum):
    """Mirror of the Rust `SandboxTier` enum's wire names."""

    TIER_0 = "tier_0"
    TIER_1 = "tier_1"
    TIER_2 = "tier_2"
    TIER_3 = "tier_3"

    @classmethod
    def parse(cls, value: str | None) -> SandboxTier | None:
        if value is None:
            return None
        try:
            return cls(value)
        except ValueError:
            return None


@dataclass(frozen=True)
class EscalationInput:
    """Everything the policy needs to pick a tier for one node."""

    requested_tier: SandboxTier | None
    description: str
    rationale: str
    user_override: SandboxTier | None = None
    user_floor: SandboxTier | None = None
    user_ceiling: SandboxTier | None = None
    available_tiers: frozenset[SandboxTier] = frozenset({SandboxTier.TIER_0, SandboxTier.TIER_1})


@dataclass(frozen=True)
class TierFallback:
    """Result of resolving a node — the chosen tier plus a warning
    when the policy had to fall back from a stronger one."""

    tier: SandboxTier
    requested: SandboxTier
    warning: str | None

    def to_wire(self) -> dict[str, Any]:
        return {
            "tier": self.tier.value,
            "requested": self.requested.value,
            "warning": self.warning,
        }


def resolve(input: EscalationInput) -> TierFallback:
    """Pick the effective tier for a sub-agent dispatch.

    Order: auto-escalation against the description, then the user's
    floor / ceiling / override, then a fallback for unavailable
    tiers.
    """
    requested = input.requested_tier or SandboxTier.TIER_1
    auto_escalated = _maybe_auto_escalate(requested, input.description, input.rationale)

    if input.user_override is not None:
        chosen = input.user_override
    else:
        chosen = auto_escalated

    if input.user_floor is not None and _rank(chosen) < _rank(input.user_floor):
        chosen = input.user_floor
    if input.user_ceiling is not None and _rank(chosen) > _rank(input.user_ceiling):
        chosen = input.user_ceiling

    if chosen in input.available_tiers:
        return TierFallback(tier=chosen, requested=requested, warning=None)

    fallback_tier = _best_available_below(chosen, input.available_tiers)
    warning = (
        f"Requested {chosen.value} but it is not available on this host; "
        f"falling back to {fallback_tier.value}. The agent's run will continue "
        "with a weaker isolation posture — review the action log if this matters."
    )
    return TierFallback(tier=fallback_tier, requested=requested, warning=warning)


def _maybe_auto_escalate(requested: SandboxTier, description: str, rationale: str) -> SandboxTier:
    if requested in {SandboxTier.TIER_2, SandboxTier.TIER_3}:
        return requested  # already at the strong tiers
    haystack = f"{description}\n{rationale}".lower()
    for hint in HIGH_RISK_HINTS:
        if hint in haystack:
            return SandboxTier.TIER_2
    return requested


def _rank(tier: SandboxTier) -> int:
    return {
        SandboxTier.TIER_0: 0,
        SandboxTier.TIER_1: 1,
        SandboxTier.TIER_2: 2,
        SandboxTier.TIER_3: 3,
    }[tier]


def _best_available_below(target: SandboxTier, available: frozenset[SandboxTier]) -> SandboxTier:
    """Pick the strongest available tier at or below ``target``.

    The policy never silently strengthens isolation past the user's
    request — if Tier 1 is unavailable but Tier 2 is, we still fall
    back, not up. Falling back to Tier 0 is the floor; the policy
    relies on Tier 0 always being available (it's a bare process and
    has no host requirements).
    """
    target_rank = _rank(target)
    candidates = sorted(
        (tier for tier in available if _rank(tier) <= target_rank),
        key=_rank,
        reverse=True,
    )
    if candidates:
        return candidates[0]
    return SandboxTier.TIER_0
