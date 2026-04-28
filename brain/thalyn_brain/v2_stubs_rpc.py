"""IPC stubs for the v2 surface — registered, not yet implemented.

Per the data-model stage, the methods every later stage relies on are
registered here so the IPC contract exists from day one. Each method
raises a ``NOT_IMPLEMENTED`` ``RpcError`` whose message names the
stage that will fill it in. The caller receives a clear, parseable
signal — neither a missing-method error nor a hand-rolled string the
renderer would have to introspect.

Stages that fill these in:

- digest.run — the eternal-thread durability stage (lands with the
  rolling-summarizer node; thread.recent / thread.search / digest.latest
  are already real handlers).
- auth.list / auth.probe / auth.set — the brain auth-backend split
  stage.
- lead.spawn / lead.list / lead.pause / lead.resume / lead.archive —
  the lead-as-first-class stage.
- routing.get / routing.set / routing.clear — the worker-routing
  stage.
- project.create / project.list — the multi-project stage.
- project.classify — the project-mobility stage.
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

_STUB_METHODS: tuple[tuple[str, str], ...] = (
    ("digest.run", "the eternal-thread durability stage"),
    ("auth.list", "the brain auth-backend split stage"),
    ("auth.probe", "the brain auth-backend split stage"),
    ("auth.set", "the brain auth-backend split stage"),
    ("lead.spawn", "the lead-as-first-class stage"),
    ("lead.list", "the lead-as-first-class stage"),
    ("lead.pause", "the lead-as-first-class stage"),
    ("lead.resume", "the lead-as-first-class stage"),
    ("lead.archive", "the lead-as-first-class stage"),
    ("routing.get", "the worker-routing stage"),
    ("routing.set", "the worker-routing stage"),
    ("routing.clear", "the worker-routing stage"),
    ("project.create", "the multi-project stage"),
    ("project.list", "the multi-project stage"),
    ("project.classify", "the project-mobility stage"),
)


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
