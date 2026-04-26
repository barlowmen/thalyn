"""JSON-RPC bindings for the schedules surface.

Three methods land:

- ``schedules.list`` — returns every persisted schedule for the
  scheduler UI.
- ``schedules.create`` — accepts ``{nlInput | cron, runTemplate,
  title?}``, validates the cron (translating from NL via the brain
  if no explicit cron was supplied), persists, and returns the
  resolved schedule.
- ``schedules.delete`` — drops a schedule by id.

The cron-translation surface lives at ``cron.translate`` so the
renderer can preview a translation before committing.
"""

from __future__ import annotations

import time

from thalyn_brain.orchestration.cron import (
    parse_cron_response,
    translate_nl_to_cron,
    validate_cron,
)
from thalyn_brain.provider import ProviderRegistry
from thalyn_brain.rpc import (
    INVALID_PARAMS,
    Dispatcher,
    JsonValue,
    RpcError,
    RpcParams,
)
from thalyn_brain.schedules import (
    Schedule,
    SchedulesStore,
    new_schedule_id,
    next_fire_ms,
)


def register_schedule_methods(
    dispatcher: Dispatcher,
    store: SchedulesStore,
    registry: ProviderRegistry,
    *,
    default_provider_id: str = "anthropic",
) -> None:
    async def schedules_list(_params: RpcParams) -> JsonValue:
        rows = await store.list_all()
        return {"schedules": [row.to_wire() for row in rows]}

    async def schedules_create(params: RpcParams) -> JsonValue:
        title = _require_str(params, "title", default="Untitled schedule")
        run_template = params.get("runTemplate")
        if not isinstance(run_template, dict):
            raise RpcError(
                code=INVALID_PARAMS,
                message="runTemplate must be an object with at least `prompt`",
            )
        prompt = run_template.get("prompt")
        if not isinstance(prompt, str) or not prompt:
            raise RpcError(
                code=INVALID_PARAMS,
                message="runTemplate.prompt must be a non-empty string",
            )

        # Cron string can be supplied directly (expert mode) or
        # derived from NL input via the translator.
        cron_input = params.get("cron")
        nl_input = params.get("nlInput", "")
        if not isinstance(nl_input, str):
            nl_input = ""
        if isinstance(cron_input, str) and cron_input.strip():
            translation = validate_cron(cron_input.strip(), nl_input=nl_input)
        else:
            if not nl_input.strip():
                raise RpcError(
                    code=INVALID_PARAMS,
                    message="either `cron` or `nlInput` is required",
                )
            provider_id = run_template.get("providerId") or default_provider_id
            try:
                provider = registry.get(provider_id)
            except Exception as exc:
                raise RpcError(
                    code=INVALID_PARAMS,
                    message=f"unknown provider {provider_id!r}: {exc}",
                ) from exc
            translation = await translate_nl_to_cron(provider, nl_input)
        if not translation.valid:
            raise RpcError(
                code=INVALID_PARAMS,
                message=translation.error or "cron expression failed validation",
                data=translation.to_wire(),
            )

        now_ms = int(time.time() * 1000)
        schedule = Schedule(
            schedule_id=new_schedule_id(),
            project_id=None,
            title=title,
            nl_input=nl_input,
            cron=translation.cron,
            run_template=run_template,
            enabled=True,
            next_run_at_ms=next_fire_ms(translation.cron, now_ms=now_ms),
            last_run_at_ms=None,
            last_run_id=None,
            created_at_ms=now_ms,
        )
        await store.insert(schedule)
        return {"schedule": schedule.to_wire()}

    async def schedules_delete(params: RpcParams) -> JsonValue:
        schedule_id = _require_str(params, "scheduleId")
        deleted = await store.delete(schedule_id)
        return {"deleted": deleted, "scheduleId": schedule_id}

    async def cron_translate(params: RpcParams) -> JsonValue:
        nl_input = _require_str(params, "nlInput")
        provider_id = params.get("providerId", default_provider_id)
        if not isinstance(provider_id, str):
            raise RpcError(code=INVALID_PARAMS, message="providerId must be a string")
        try:
            provider = registry.get(provider_id)
        except Exception as exc:
            raise RpcError(
                code=INVALID_PARAMS,
                message=f"unknown provider {provider_id!r}: {exc}",
            ) from exc
        translation = await translate_nl_to_cron(provider, nl_input)
        return translation.to_wire()

    dispatcher.register("schedules.list", schedules_list)
    dispatcher.register("schedules.create", schedules_create)
    dispatcher.register("schedules.delete", schedules_delete)
    dispatcher.register("cron.translate", cron_translate)


def _require_str(params: RpcParams, key: str, *, default: str | None = None) -> str:
    value = params.get(key, default)
    if isinstance(value, str) and value.strip():
        return value
    if default is not None and value == default:
        return default
    raise RpcError(code=INVALID_PARAMS, message=f"missing or non-string {key!r}")


# Re-export so test code can build canned responses.
__all__ = [
    "parse_cron_response",
    "register_schedule_methods",
]
