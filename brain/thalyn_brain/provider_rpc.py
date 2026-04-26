"""JSON-RPC bindings for provider metadata + capability deltas."""

from __future__ import annotations

from thalyn_brain.provider import (
    ProviderRegistry,
    compare_profiles,
)
from thalyn_brain.rpc import (
    INVALID_PARAMS,
    Dispatcher,
    JsonValue,
    RpcError,
    RpcParams,
)


def register_provider_methods(dispatcher: Dispatcher, registry: ProviderRegistry) -> None:
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
        delta = compare_profiles(
            from_id=from_id,
            from_profile=from_provider.capability_profile,
            to_id=to_id,
            to_profile=to_provider.capability_profile,
        )
        return delta.to_wire()

    dispatcher.register("providers.list", providers_list)
    dispatcher.register("providers.delta", providers_delta)


def _require_str(params: RpcParams, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value:
        raise RpcError(code=INVALID_PARAMS, message=f"missing or non-string {key!r}")
    return value
