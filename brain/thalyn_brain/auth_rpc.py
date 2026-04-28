"""JSON-RPC bindings for the auth-backend surface.

Three methods replace the v0.21 ``NOT_IMPLEMENTED`` stubs:

- ``auth.list`` — enumerate all auth backends with their probe state
  and which one is currently active.
- ``auth.probe`` — re-run the probe for one backend (the wizard uses
  this to refresh after the user installs / logs in).
- ``auth.set`` — mark one backend as active for this brain.

Backed by ``AuthBackendRegistry``. Responses use camelCase keys to
match the rest of the brain's IPC contract.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from thalyn_brain.auth_registry import AuthBackendRegistry
from thalyn_brain.provider.auth import AuthBackend, AuthBackendError, AuthBackendKind
from thalyn_brain.rpc import (
    INVALID_PARAMS,
    Dispatcher,
    JsonValue,
    RpcError,
    RpcParams,
)


def register_auth_methods(
    dispatcher: Dispatcher,
    registry: AuthBackendRegistry,
    *,
    on_active_changed: Callable[[AuthBackend], None] | None = None,
) -> None:
    """Wire ``auth.list`` / ``auth.probe`` / ``auth.set`` onto
    ``dispatcher``."""

    async def auth_list(_params: RpcParams) -> JsonValue:
        backends: list[dict[str, Any]] = []
        for kind in registry.list_kinds():
            descriptor = registry.descriptor(kind)
            descriptor["probe"] = (await registry.probe(kind)).to_wire()
            backends.append(descriptor)
        return {
            "activeKind": registry.active_kind.value,
            "backends": backends,
        }

    async def auth_probe(params: RpcParams) -> JsonValue:
        kind = _require_kind(params)
        result = await registry.probe(kind)
        return {"kind": kind.value, "probe": result.to_wire()}

    async def auth_set(params: RpcParams) -> JsonValue:
        kind = _require_kind(params)
        try:
            registry.set_active(kind)
        except AuthBackendError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        # Notify any subscriber (typically the AnthropicProvider) so the
        # next chat turn uses the new credential.
        if on_active_changed is not None:
            on_active_changed(registry.active())
        # Surface the fresh probe so the renderer can decide whether to
        # warn the user about a not-yet-authenticated active selection.
        result = await registry.probe(kind)
        return {
            "activeKind": registry.active_kind.value,
            "probe": result.to_wire(),
        }

    dispatcher.register("auth.list", auth_list)
    dispatcher.register("auth.probe", auth_probe)
    dispatcher.register("auth.set", auth_set)


def _require_kind(params: RpcParams) -> AuthBackendKind:
    raw = params.get("kind")
    if not isinstance(raw, str) or not raw:
        raise RpcError(
            code=INVALID_PARAMS,
            message="missing or non-string 'kind'",
        )
    try:
        return AuthBackendKind(raw)
    except ValueError as exc:
        raise RpcError(
            code=INVALID_PARAMS,
            message=f"unknown auth backend kind: {raw}",
        ) from exc
