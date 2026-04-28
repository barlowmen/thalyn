"""JSON-RPC bindings for provider metadata + capability deltas + model download."""

from __future__ import annotations

from thalyn_brain.auth_registry import AuthBackendRegistry
from thalyn_brain.provider import (
    AuthBackendKind,
    ProviderRegistry,
    compare_profiles,
)
from thalyn_brain.provider.model_download import (
    check_mlx_model,
    check_ollama_model,
    pull_ollama_model,
)
from thalyn_brain.provider.ollama import DEFAULT_BASE_URL as OLLAMA_BASE_URL
from thalyn_brain.rpc import (
    INVALID_PARAMS,
    Dispatcher,
    JsonValue,
    Notifier,
    RpcError,
    RpcParams,
)

# Provider id → the auth-backend kind it uses when no Anthropic-family
# choice is in play. Anthropic providers' auth kind is read from the
# AuthBackendRegistry's active kind (it can be subscription or API key
# at any moment); the rest are tied to their substrate.
_PROVIDER_AUTH_KIND: dict[str, AuthBackendKind] = {
    "ollama": AuthBackendKind.OLLAMA,
    "llama_cpp": AuthBackendKind.LLAMA_CPP,
    "mlx": AuthBackendKind.MLX,
    "openai_compat": AuthBackendKind.OPENAI_COMPAT,
}

_ANTHROPIC_FAMILY_KINDS = frozenset(
    {AuthBackendKind.CLAUDE_SUBSCRIPTION, AuthBackendKind.ANTHROPIC_API}
)


def auth_kind_for_provider(
    provider_id: str,
    *,
    auth_registry: AuthBackendRegistry | None = None,
) -> AuthBackendKind | None:
    """Derive the auth-backend kind that fronts ``provider_id``.

    Anthropic reads from the live ``AuthBackendRegistry`` so a swap
    between subscription and API-key auth is reflected immediately.
    Local / OpenAI-compat providers carry their own auth substrate and
    do not consult the registry. Unknown providers return ``None`` so
    the diff can omit the row gracefully.
    """
    if provider_id == "anthropic":
        if auth_registry is None:
            return AuthBackendKind.CLAUDE_SUBSCRIPTION
        active = auth_registry.active_kind
        if active in _ANTHROPIC_FAMILY_KINDS:
            return active
        # Active kind is non-Anthropic (Ollama / Mlx / etc.). Fall back
        # to the family default so the dialog still shows something
        # sensible if the user lands on the Anthropic provider after
        # picking a local backend.
        return AuthBackendKind.CLAUDE_SUBSCRIPTION
    return _PROVIDER_AUTH_KIND.get(provider_id)


def register_provider_methods(
    dispatcher: Dispatcher,
    registry: ProviderRegistry,
    *,
    auth_registry: AuthBackendRegistry | None = None,
) -> None:
    async def providers_list(_params: RpcParams) -> JsonValue:
        metas = registry.list_meta()
        return {"providers": [meta.to_wire() for meta in metas]}

    async def providers_delta(params: RpcParams) -> JsonValue:
        from_id = _require_str(params, "fromId")
        to_id = _require_str(params, "toId")
        try:
            from_provider = registry.get(from_id)
            to_provider = registry.get(to_id)
        except Exception as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        from_auth = auth_kind_for_provider(from_id, auth_registry=auth_registry)
        to_auth = auth_kind_for_provider(to_id, auth_registry=auth_registry)
        delta = compare_profiles(
            from_id=from_id,
            from_profile=from_provider.capability_profile,
            to_id=to_id,
            to_profile=to_provider.capability_profile,
            from_auth_kind=from_auth.value if from_auth is not None else None,
            to_auth_kind=to_auth.value if to_auth is not None else None,
        )
        return delta.to_wire()

    async def providers_check_model(params: RpcParams) -> JsonValue:
        provider_id = _require_str(params, "providerId")
        model = _require_str(params, "model")
        if provider_id == "ollama":
            base_url_value = params.get("baseUrl")
            base_url = base_url_value if isinstance(base_url_value, str) else OLLAMA_BASE_URL
            status = await check_ollama_model(base_url=base_url, model=model)
            return status.to_wire()
        if provider_id == "mlx":
            return check_mlx_model(model=model).to_wire()
        raise RpcError(
            code=INVALID_PARAMS,
            message=f"providers.check_model does not support {provider_id!r}",
        )

    async def providers_pull_model(params: RpcParams, notify: Notifier) -> JsonValue:
        provider_id = _require_str(params, "providerId")
        model = _require_str(params, "model")
        if provider_id != "ollama":
            raise RpcError(
                code=INVALID_PARAMS,
                message=f"providers.pull_model does not support {provider_id!r}",
            )
        base_url_value = params.get("baseUrl")
        base_url = base_url_value if isinstance(base_url_value, str) else OLLAMA_BASE_URL

        last_status = "unknown"
        async for progress in pull_ollama_model(base_url=base_url, model=model):
            last_status = progress.status
            await notify(
                "providers.pull_progress",
                {"providerId": provider_id, "model": model, **progress.to_wire()},
            )
        return {"providerId": provider_id, "model": model, "status": last_status}

    dispatcher.register("providers.list", providers_list)
    dispatcher.register("providers.delta", providers_delta)
    dispatcher.register("providers.check_model", providers_check_model)
    dispatcher.register_streaming("providers.pull_model", providers_pull_model)


def _require_str(params: RpcParams, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise RpcError(code=INVALID_PARAMS, message=f"missing or non-string {key!r}")
    return value
