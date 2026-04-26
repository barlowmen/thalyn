"""JSON-RPC bindings for provider metadata + capability deltas + model download."""

from __future__ import annotations

from thalyn_brain.provider import (
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
