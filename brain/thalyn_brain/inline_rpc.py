"""JSON-RPC binding for ghost-text inline-suggest."""

from __future__ import annotations

import uuid

from thalyn_brain.inline import suggest
from thalyn_brain.provider import ProviderRegistry
from thalyn_brain.rpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    Dispatcher,
    JsonValue,
    RpcError,
    RpcParams,
)


def register_inline_methods(dispatcher: Dispatcher, registry: ProviderRegistry) -> None:
    async def inline_suggest(params: RpcParams) -> JsonValue:
        provider_id = _require_str(params, "providerId")
        prefix = _require_str(params, "prefix", allow_empty=True)
        suffix_value = params.get("suffix", "")
        suffix = suffix_value if isinstance(suffix_value, str) else ""
        language_value = params.get("language", "")
        language = language_value if isinstance(language_value, str) else ""
        request_id_value = params.get("requestId")
        request_id = (
            request_id_value
            if isinstance(request_id_value, str) and request_id_value
            else f"inline_{uuid.uuid4().hex[:12]}"
        )

        try:
            provider = registry.get(provider_id)
        except KeyError as exc:
            raise RpcError(
                code=INVALID_PARAMS,
                message=f"unknown provider: {provider_id}",
            ) from exc

        try:
            result = await suggest(
                provider=provider,
                provider_id=provider_id,
                request_id=request_id,
                prefix=prefix,
                suffix=suffix,
                language=language,
            )
        except Exception as exc:
            raise RpcError(code=INTERNAL_ERROR, message=str(exc)) from exc
        return result.to_wire()

    dispatcher.register("inline.suggest", inline_suggest)


def _require_str(params: RpcParams, key: str, *, allow_empty: bool = False) -> str:
    value = params.get(key)
    if not isinstance(value, str):
        raise RpcError(code=INVALID_PARAMS, message=f"missing or non-string {key!r}")
    if not allow_empty and not value.strip():
        raise RpcError(code=INVALID_PARAMS, message=f"{key} must not be empty")
    return value
