"""IPC stubs for the v2 surface — placeholder for future entry points.

Per the data-model stage, methods every later stage relies on are
registered here so the IPC contract exists from day one. Each method
raises a ``NOT_IMPLEMENTED`` ``RpcError`` whose message names the
stage that will fill it in. The caller receives a clear, parseable
signal — neither a missing-method error nor a hand-rolled string the
renderer would have to introspect.

Every method that originally lived here has now landed:

- ``project.create`` / ``project.list`` / ``project.update`` /
  ``project.archive`` / ``project.pause`` / ``project.resume`` /
  ``project.classify`` register themselves through
  ``project_rpc.register_project_methods``.
- ``auth.*`` register through ``auth_rpc.register_auth_methods``.
- ``lead.*`` register through ``lead_rpc.register_lead_methods``.
- ``routing.*`` register through
  ``routing_rpc.register_routing_methods``.

The module is kept as a no-op registration point so future entry
points the harness wants to scaffold ahead of their stage land here
without re-introducing the helper.
"""

from __future__ import annotations

from thalyn_brain.rpc import (
    NOT_IMPLEMENTED,
    Dispatcher,
    JsonValue,
    PlainHandler,
    RpcError,
    RpcParams,
)

_STUB_METHODS: tuple[tuple[str, str], ...] = ()


def register_v2_stubs(dispatcher: Dispatcher) -> None:
    """Register every v2 stub method on ``dispatcher``.

    The stubs all raise ``NOT_IMPLEMENTED``. Real implementations
    register their own handlers in the stages that own them; the
    ``Dispatcher`` already errors on duplicate registration, so the
    real handler will need to land *instead of* the stub, not
    alongside.
    """
    for method, stage_name in _STUB_METHODS:
        dispatcher.register(method, _make_stub(method, stage_name))


def _make_stub(method: str, stage_name: str) -> PlainHandler:
    """Build an async handler for ``method`` that raises NOT_IMPLEMENTED.

    Returns the handler as a closure so each method gets a unique
    function object the dispatcher can register.
    """

    async def stub(_params: RpcParams) -> JsonValue:
        raise RpcError(
            code=NOT_IMPLEMENTED,
            message=f"{method}: not yet implemented (lands in {stage_name})",
        )

    return stub
