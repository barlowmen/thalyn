"""JSON-RPC bindings for the brain's browser session.

Renderer-facing methods (`browser.attach`, `browser.detach`,
`browser.status`) drive the lifecycle the Rust core's
``BrowserManager`` triggers when Chromium spawns. Agent-facing
methods (`browser.navigate`, `browser.get_text`, `browser.click`,
`browser.type`, `browser.screenshot`) are the primitives the agent
tool wraps. Both surfaces share one :class:`BrowserManager`.
"""

from __future__ import annotations

from thalyn_brain.browser import (
    BrowserAlreadyAttachedError,
    BrowserError,
    BrowserManager,
    BrowserNotAttachedError,
)
from thalyn_brain.rpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    Dispatcher,
    JsonValue,
    RpcError,
    RpcParams,
)


def register_browser_methods(dispatcher: Dispatcher, manager: BrowserManager) -> None:
    async def browser_attach(params: RpcParams) -> JsonValue:
        ws_url = _require_str(params, "wsUrl")
        try:
            info = await manager.attach(ws_url)
        except BrowserAlreadyAttachedError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        except BrowserError as exc:
            raise RpcError(code=INTERNAL_ERROR, message=str(exc)) from exc
        return {"attached": True, **info.to_wire()}

    async def browser_detach(_: RpcParams) -> JsonValue:
        detached = await manager.detach()
        return {"detached": detached}

    async def browser_status(_: RpcParams) -> JsonValue:
        info = manager.attached_info()
        return {
            "attached": info is not None,
            "session": info.to_wire() if info is not None else None,
        }

    async def browser_navigate(params: RpcParams) -> JsonValue:
        url = _require_str(params, "url")
        try:
            result = await manager.navigate(url)
        except BrowserNotAttachedError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        except BrowserError as exc:
            raise RpcError(code=INTERNAL_ERROR, message=str(exc)) from exc
        return result.to_wire()

    async def browser_get_text(params: RpcParams) -> JsonValue:
        selector_value = params.get("selector")
        selector = selector_value if isinstance(selector_value, str) else None
        try:
            result = await manager.get_text(selector)
        except BrowserNotAttachedError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        except BrowserError as exc:
            raise RpcError(code=INTERNAL_ERROR, message=str(exc)) from exc
        return result.to_wire()

    async def browser_click(params: RpcParams) -> JsonValue:
        selector = _require_str(params, "selector")
        try:
            result = await manager.click(selector)
        except BrowserNotAttachedError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        except BrowserError as exc:
            raise RpcError(code=INTERNAL_ERROR, message=str(exc)) from exc
        return result.to_wire()

    async def browser_type(params: RpcParams) -> JsonValue:
        selector = _require_str(params, "selector")
        text = _require_str(params, "text", allow_empty=True)
        try:
            result = await manager.type_text(selector, text)
        except BrowserNotAttachedError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        except BrowserError as exc:
            raise RpcError(code=INTERNAL_ERROR, message=str(exc)) from exc
        return result.to_wire()

    async def browser_screenshot(_: RpcParams) -> JsonValue:
        try:
            result = await manager.screenshot()
        except BrowserNotAttachedError as exc:
            raise RpcError(code=INVALID_PARAMS, message=str(exc)) from exc
        except BrowserError as exc:
            raise RpcError(code=INTERNAL_ERROR, message=str(exc)) from exc
        return result.to_wire()

    dispatcher.register("browser.attach", browser_attach)
    dispatcher.register("browser.detach", browser_detach)
    dispatcher.register("browser.status", browser_status)
    dispatcher.register("browser.navigate", browser_navigate)
    dispatcher.register("browser.get_text", browser_get_text)
    dispatcher.register("browser.click", browser_click)
    dispatcher.register("browser.type", browser_type)
    dispatcher.register("browser.screenshot", browser_screenshot)


def _require_str(params: RpcParams, key: str, *, allow_empty: bool = False) -> str:
    value = params.get(key)
    if not isinstance(value, str):
        raise RpcError(code=INVALID_PARAMS, message=f"missing or non-string {key!r}")
    if not allow_empty and not value.strip():
        raise RpcError(code=INVALID_PARAMS, message=f"{key!r} must be non-empty")
    return value
