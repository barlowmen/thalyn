"""Capability-delta tests — profile diff + JSON-RPC bindings."""

from __future__ import annotations

from typing import Any

from thalyn_brain.auth_registry import AuthBackendRegistry
from thalyn_brain.provider import (
    AuthBackendKind,
    CapabilityProfile,
    ReliabilityTier,
    build_registry,
    compare_profiles,
)
from thalyn_brain.provider_rpc import auth_kind_for_provider, register_provider_methods
from thalyn_brain.rpc import Dispatcher


def _profile(
    *,
    max_context_tokens: int = 200_000,
    supports_tool_use: bool = True,
    tool_use_reliability: ReliabilityTier = ReliabilityTier.HIGH,
    supports_vision: bool = True,
    supports_streaming: bool = True,
    local: bool = False,
) -> CapabilityProfile:
    return CapabilityProfile(
        max_context_tokens=max_context_tokens,
        supports_tool_use=supports_tool_use,
        tool_use_reliability=tool_use_reliability,
        supports_vision=supports_vision,
        supports_streaming=supports_streaming,
        local=local,
    )


def test_identical_profiles_produce_empty_delta() -> None:
    profile = _profile()
    delta = compare_profiles(
        from_id="anthropic",
        from_profile=profile,
        to_id="anthropic",
        to_profile=profile,
    )
    assert delta.is_empty
    assert delta.changes == []


def test_swap_to_local_marks_reliability_downgrade_as_warning() -> None:
    cloud = _profile()
    local = _profile(
        max_context_tokens=32_768,
        tool_use_reliability=ReliabilityTier.MEDIUM,
        supports_vision=False,
        local=True,
    )
    delta = compare_profiles(
        from_id="anthropic",
        from_profile=cloud,
        to_id="ollama",
        to_profile=local,
    )
    by_dim = {change.dimension: change for change in delta.changes}
    assert by_dim["maxContextTokens"].severity == "warning"
    assert by_dim["maxContextTokens"].before == 200_000
    assert by_dim["maxContextTokens"].after == 32_768
    assert by_dim["toolUseReliability"].severity == "warning"
    assert by_dim["toolUseReliability"].before == "high"
    assert by_dim["toolUseReliability"].after == "medium"
    assert by_dim["supportsVision"].severity == "warning"
    assert by_dim["local"].severity == "info"


def test_swap_back_to_cloud_marks_reliability_upgrade_as_info() -> None:
    local = _profile(
        max_context_tokens=32_768,
        tool_use_reliability=ReliabilityTier.MEDIUM,
        supports_vision=False,
        local=True,
    )
    cloud = _profile()
    delta = compare_profiles(
        from_id="ollama",
        from_profile=local,
        to_id="anthropic",
        to_profile=cloud,
    )
    by_dim = {change.dimension: change for change in delta.changes}
    assert by_dim["maxContextTokens"].severity == "info"
    assert by_dim["toolUseReliability"].severity == "info"


def test_drop_tool_use_entirely_marks_a_warning() -> None:
    with_tools = _profile(supports_tool_use=True)
    without_tools = _profile(supports_tool_use=False, tool_use_reliability=ReliabilityTier.LOW)
    delta = compare_profiles(
        from_id="anthropic",
        from_profile=with_tools,
        to_id="mlx",
        to_profile=without_tools,
    )
    by_dim = {change.dimension: change for change in delta.changes}
    assert by_dim["supportsToolUse"].severity == "warning"
    assert by_dim["supportsToolUse"].before is True
    assert by_dim["supportsToolUse"].after is False


def test_delta_to_wire_is_camel_cased() -> None:
    delta = compare_profiles(
        from_id="anthropic",
        from_profile=_profile(),
        to_id="ollama",
        to_profile=_profile(local=True),
    )
    wire = delta.to_wire()
    assert wire["fromProviderId"] == "anthropic"
    assert wire["toProviderId"] == "ollama"
    assert all("dimension" in change for change in wire["changes"])


# ---------------------------------------------------------------------------
# JSON-RPC surface
# ---------------------------------------------------------------------------


async def test_providers_list_returns_metas() -> None:
    registry = build_registry()
    dispatcher = Dispatcher()
    register_provider_methods(dispatcher, registry)

    async def notify(method: str, params: Any) -> None:
        del method, params

    response = await dispatcher.handle(
        {"jsonrpc": "2.0", "id": 1, "method": "providers.list"},
        notify,
    )
    assert response is not None
    providers = response["result"]["providers"]
    ids = {provider["id"] for provider in providers}
    assert {"anthropic", "ollama", "mlx"} <= ids


async def test_providers_delta_diffs_two_real_providers() -> None:
    registry = build_registry()
    dispatcher = Dispatcher()
    register_provider_methods(dispatcher, registry)

    async def notify(method: str, params: Any) -> None:
        del method, params

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "providers.delta",
            "params": {"fromId": "anthropic", "toId": "ollama"},
        },
        notify,
    )
    assert response is not None
    result = response["result"]
    assert result["fromProviderId"] == "anthropic"
    assert result["toProviderId"] == "ollama"
    dimensions = {change["dimension"] for change in result["changes"]}
    # Anthropic → Ollama drops max-context, downgrades tool-use
    # reliability, and flips local to True.
    assert "maxContextTokens" in dimensions
    assert "toolUseReliability" in dimensions
    assert "local" in dimensions


async def test_providers_delta_unknown_id_returns_invalid_params() -> None:
    registry = build_registry()
    dispatcher = Dispatcher()
    register_provider_methods(dispatcher, registry)

    async def notify(method: str, params: Any) -> None:
        del method, params

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "providers.delta",
            "params": {"fromId": "anthropic", "toId": "ghost"},
        },
        notify,
    )
    assert response is not None
    assert response["error"]["code"] == -32602


# ---------------------------------------------------------------------------
# Auth-backend dimension (ADR-0020)
# ---------------------------------------------------------------------------


def test_compare_profiles_omits_auth_row_when_kinds_omitted() -> None:
    """Backwards-compatible: callers that don't pass auth kinds still
    get the v1 capability-only diff."""
    profile = _profile()
    delta = compare_profiles(
        from_id="anthropic",
        from_profile=profile,
        to_id="anthropic",
        to_profile=profile,
    )
    assert delta.is_empty


def test_compare_profiles_emits_auth_row_when_kinds_differ() -> None:
    profile = _profile()
    delta = compare_profiles(
        from_id="anthropic",
        from_profile=profile,
        to_id="anthropic",
        to_profile=profile,
        from_auth_kind="claude_subscription",
        to_auth_kind="anthropic_api",
    )
    dimensions = {change.dimension for change in delta.changes}
    assert dimensions == {"authBackend"}
    auth_change = delta.changes[0]
    assert auth_change.before == "claude_subscription"
    assert auth_change.after == "anthropic_api"
    # Auth-backend changes are informational; the capability profile
    # is what tells the user about downgrades.
    assert auth_change.severity == "info"


def test_compare_profiles_omits_auth_row_when_kinds_match() -> None:
    profile = _profile()
    delta = compare_profiles(
        from_id="anthropic",
        from_profile=profile,
        to_id="anthropic",
        to_profile=profile,
        from_auth_kind="claude_subscription",
        to_auth_kind="claude_subscription",
    )
    assert delta.is_empty


def test_auth_kind_for_anthropic_reads_active_registry_kind() -> None:
    registry = AuthBackendRegistry(active_kind=AuthBackendKind.ANTHROPIC_API)
    kind = auth_kind_for_provider("anthropic", auth_registry=registry)
    assert kind == AuthBackendKind.ANTHROPIC_API


def test_auth_kind_for_anthropic_falls_back_when_active_is_non_anthropic() -> None:
    """If the user is currently on Ollama and views the Anthropic
    capability profile, fall back to the subscription default rather
    than reporting an Ollama-flavoured auth kind."""
    registry = AuthBackendRegistry(active_kind=AuthBackendKind.OLLAMA)
    kind = auth_kind_for_provider("anthropic", auth_registry=registry)
    assert kind == AuthBackendKind.CLAUDE_SUBSCRIPTION


def test_auth_kind_for_local_providers_is_substrate_tied() -> None:
    registry = AuthBackendRegistry(active_kind=AuthBackendKind.CLAUDE_SUBSCRIPTION)
    assert auth_kind_for_provider("ollama", auth_registry=registry) == AuthBackendKind.OLLAMA
    assert auth_kind_for_provider("llama_cpp", auth_registry=registry) == AuthBackendKind.LLAMA_CPP
    assert auth_kind_for_provider("mlx", auth_registry=registry) == AuthBackendKind.MLX
    assert (
        auth_kind_for_provider("openai_compat", auth_registry=registry)
        == AuthBackendKind.OPENAI_COMPAT
    )


def test_auth_kind_for_unknown_provider_returns_none() -> None:
    assert auth_kind_for_provider("ghost") is None


async def test_providers_delta_includes_auth_row_when_providers_differ() -> None:
    """Anthropic → Ollama: the auth substrate flips from
    claude_subscription to ollama and the dialog shows it."""
    registry = build_registry()
    auth_registry = AuthBackendRegistry()
    dispatcher = Dispatcher()
    register_provider_methods(dispatcher, registry, auth_registry=auth_registry)

    async def notify(method: str, params: Any) -> None:
        del method, params

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "providers.delta",
            "params": {"fromId": "anthropic", "toId": "ollama"},
        },
        notify,
    )
    assert response is not None
    changes = response["result"]["changes"]
    auth_changes = [c for c in changes if c["dimension"] == "authBackend"]
    assert len(auth_changes) == 1
    assert auth_changes[0]["before"] == "claude_subscription"
    assert auth_changes[0]["after"] == "ollama"


async def test_providers_delta_omits_auth_row_when_anthropic_auth_steady() -> None:
    """anthropic → anthropic with the same active auth kind: no auth row."""
    registry = build_registry()
    auth_registry = AuthBackendRegistry(active_kind=AuthBackendKind.ANTHROPIC_API)
    dispatcher = Dispatcher()
    register_provider_methods(dispatcher, registry, auth_registry=auth_registry)

    async def notify(method: str, params: Any) -> None:
        del method, params

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "providers.delta",
            "params": {"fromId": "anthropic", "toId": "anthropic"},
        },
        notify,
    )
    assert response is not None
    changes = response["result"]["changes"]
    assert all(change["dimension"] != "authBackend" for change in changes)


async def test_providers_delta_omits_auth_row_when_registry_unwired() -> None:
    """Backwards-compatible: callers that haven't wired the
    auth_registry still get the v1 capability-only diff."""
    registry = build_registry()
    dispatcher = Dispatcher()
    register_provider_methods(dispatcher, registry)  # no auth_registry kwarg

    async def notify(method: str, params: Any) -> None:
        del method, params

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "providers.delta",
            "params": {"fromId": "anthropic", "toId": "ollama"},
        },
        notify,
    )
    assert response is not None
    changes = response["result"]["changes"]
    auth_changes = [c for c in changes if c["dimension"] == "authBackend"]
    # Without a registry, anthropic still falls back to subscription
    # (the default), but the diff still emits the row because the
    # local providers carry their own substrate.
    assert len(auth_changes) == 1
    assert auth_changes[0]["before"] == "claude_subscription"
    assert auth_changes[0]["after"] == "ollama"
