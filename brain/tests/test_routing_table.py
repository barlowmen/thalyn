"""Unit tests for ``thalyn_brain.routing_table``.

The routing function is pure — these cover the resolution-order matrix
end-to-end without spinning up SQLite or a provider registry. The
integration test that asserts a spawn actually picks the routed
provider lives in ``test_runner_routing``.
"""

from __future__ import annotations

import platform

import pytest
from thalyn_brain.routing_table import (
    DEFAULT_GLOBAL_DEFAULTS,
    MatchedRule,
    normalize_task_tag,
    route_worker,
    select_local_provider_for,
)


def test_route_with_no_overrides_uses_global_default() -> None:
    decision = route_worker(task_tag="coding")
    assert decision.provider_id == "anthropic"
    assert decision.matched is MatchedRule.GLOBAL
    assert decision.effective_tag == "coding"
    assert decision.task_tag == "coding"


def test_route_falls_through_to_default_for_unknown_tag() -> None:
    decision = route_worker(task_tag="not-a-real-tag")
    assert decision.provider_id == "anthropic"
    assert decision.matched is MatchedRule.GLOBAL
    assert decision.effective_tag == "default"
    # The raw tag is preserved so audit logs reflect what the caller
    # actually passed, not what the resolver collapsed it to.
    assert decision.task_tag == "not-a-real-tag"


def test_route_uses_default_when_tag_is_none() -> None:
    decision = route_worker(task_tag=None)
    assert decision.provider_id == "anthropic"
    assert decision.effective_tag == "default"
    assert decision.matched is MatchedRule.GLOBAL


def test_route_respects_project_override() -> None:
    decision = route_worker(
        task_tag="coding",
        project_overrides={"coding": "ollama"},
    )
    assert decision.provider_id == "ollama"
    assert decision.matched is MatchedRule.OVERRIDE


def test_route_override_does_not_apply_to_unmatched_tag() -> None:
    decision = route_worker(
        task_tag="research",
        project_overrides={"coding": "ollama"},
    )
    assert decision.provider_id == "anthropic"
    assert decision.matched is MatchedRule.GLOBAL


def test_route_local_only_short_circuits_overrides() -> None:
    decision = route_worker(
        task_tag="coding",
        project_overrides={"coding": "anthropic"},
        project_local_only=True,
    )
    assert decision.matched is MatchedRule.LOCAL_ONLY
    # Local provider is platform-driven; the value here matches
    # ``select_local_provider_for`` for the test platform.
    assert decision.provider_id == select_local_provider_for("coding")


def test_route_local_only_short_circuits_global_default() -> None:
    decision = route_worker(task_tag="coding", project_local_only=True)
    assert decision.matched is MatchedRule.LOCAL_ONLY
    assert decision.provider_id in {"mlx", "ollama"}


def test_select_local_provider_picks_per_platform() -> None:
    chosen = select_local_provider_for("coding")
    if platform.system() == "Darwin":
        assert chosen == "mlx"
    else:
        assert chosen == "ollama"


def test_normalize_task_tag_lowercases_and_strips() -> None:
    assert normalize_task_tag("  CODING\n") == "coding"
    assert normalize_task_tag(None) == "default"
    assert normalize_task_tag("madeup") == "default"


def test_route_decision_audit_payload_shape() -> None:
    decision = route_worker(
        task_tag="coding",
        project_overrides={"coding": "ollama"},
    )
    payload = decision.to_audit_payload(project_id="proj_42")
    assert payload == {
        "action": "route_worker",
        "projectId": "proj_42",
        "taskTag": "coding",
        "effectiveTag": "coding",
        "providerId": "ollama",
        "matched": "override",
    }


def test_default_global_defaults_cover_every_built_in_tag() -> None:
    # Smoke-check: every tag in the built-in vocabulary has a route.
    for tag in {"default", "coding", "image", "research", "writing", "quick"}:
        assert tag in DEFAULT_GLOBAL_DEFAULTS, f"missing default for {tag}"


def test_route_caller_supplied_global_defaults_must_have_default_key() -> None:
    # Programming-error case: a caller that supplies ``global_defaults``
    # without a ``default`` row gets a ``KeyError`` so the bug surfaces
    # at the call site rather than a silent empty-string provider.
    with pytest.raises(KeyError):
        route_worker(
            task_tag="madeup",
            global_defaults={"coding": "anthropic"},
        )
