"""Console entry point: serve JSON-RPC over stdio."""

from __future__ import annotations

import asyncio
import sys

from thalyn_brain.agents import AgentRecordsStore
from thalyn_brain.approval_rpc import register_approval_methods
from thalyn_brain.auth_registry import AuthBackendRegistry
from thalyn_brain.auth_rpc import register_auth_methods
from thalyn_brain.browser import BrowserManager
from thalyn_brain.browser_rpc import register_browser_methods
from thalyn_brain.chat import register_chat_methods
from thalyn_brain.email import EmailAccountStore, EmailManager
from thalyn_brain.email.credentials import EmailCredentialsCache
from thalyn_brain.email_rpc import register_email_methods
from thalyn_brain.error_reporting import init_sentry
from thalyn_brain.inline_rpc import register_inline_methods
from thalyn_brain.lead_lifecycle import LeadLifecycle
from thalyn_brain.lead_rpc import register_lead_methods
from thalyn_brain.lsp import LspManager
from thalyn_brain.lsp_rpc import register_lsp_methods
from thalyn_brain.mcp import ConnectorRegistry, McpManager, builtin_catalog
from thalyn_brain.mcp_rpc import register_mcp_methods
from thalyn_brain.memory import MemoryStore
from thalyn_brain.memory_rpc import register_memory_methods
from thalyn_brain.orchestration import Runner
from thalyn_brain.orchestration.resume import resume_unfinished_runs
from thalyn_brain.orchestration.storage import (
    apply_pending_migrations,
    default_data_dir,
)
from thalyn_brain.projects import ProjectsStore
from thalyn_brain.provider import AnthropicProvider, build_registry
from thalyn_brain.provider.auth import AuthBackend
from thalyn_brain.provider_rpc import register_provider_methods
from thalyn_brain.rpc import build_default_dispatcher
from thalyn_brain.runs import RunsStore
from thalyn_brain.runs_rpc import register_runs_methods
from thalyn_brain.schedules import (
    Schedule,
    SchedulerLoop,
    SchedulesStore,
)
from thalyn_brain.schedules_rpc import register_schedule_methods
from thalyn_brain.terminal_observer import TerminalObserver
from thalyn_brain.terminal_rpc import register_terminal_methods
from thalyn_brain.thread_send import register_thread_send_methods
from thalyn_brain.threads import ThreadsStore
from thalyn_brain.threads_rpc import register_thread_methods
from thalyn_brain.tracing import init_tracer
from thalyn_brain.transport import serve_stdio
from thalyn_brain.v2_stubs_rpc import register_v2_stubs


def main() -> int:
    # Init tracing as early as possible so the SDK is ready before
    # the first run can fire. The default exporter is no-op (no
    # network) — set THALYN_OTEL_OTLP_ENDPOINT to ship traces.
    init_tracer()
    # Crash reporting is opt-in via THALYN_SENTRY_DSN (the Rust
    # core sets the env var from the OS keychain entry the user
    # paste their own DSN into). With no DSN, this is a no-op.
    init_sentry()
    data_dir = default_data_dir()
    apply_pending_migrations(data_dir=data_dir)
    dispatcher = build_default_dispatcher()
    # Auth-backend registry composes the AnthropicProvider's initial
    # auth at startup; ``auth.set`` notifies it so subsequent chat turns
    # use the user's chosen backend (per ADR-0020).
    auth_registry = AuthBackendRegistry()
    registry = build_registry(anthropic_auth=auth_registry.active())
    runs_store = RunsStore(data_dir=data_dir)
    schedules_store = SchedulesStore(data_dir=data_dir)
    memory_store = MemoryStore(data_dir=data_dir)
    threads_store = ThreadsStore(data_dir=data_dir)
    agent_records_store = AgentRecordsStore(data_dir=data_dir)
    projects_store = ProjectsStore(data_dir=data_dir)
    lead_lifecycle = LeadLifecycle(
        agents=agent_records_store,
        projects=projects_store,
    )
    lsp_manager = LspManager()
    terminal_observer = TerminalObserver()
    browser_manager = BrowserManager()
    connector_registry = ConnectorRegistry(data_dir=data_dir)
    mcp_manager = McpManager(registry=connector_registry, catalog=builtin_catalog())
    email_store = EmailAccountStore(data_dir=data_dir)
    email_credentials = EmailCredentialsCache()
    email_manager = EmailManager(store=email_store, token_source=email_credentials.token_source)
    runner = Runner(registry, runs_store=runs_store, data_dir=data_dir)
    register_chat_methods(dispatcher, registry, runner=runner)
    register_approval_methods(dispatcher, runner)
    register_runs_methods(dispatcher, runs_store, runner=runner)
    register_schedule_methods(dispatcher, schedules_store, registry)
    register_provider_methods(dispatcher, registry, auth_registry=auth_registry)
    register_memory_methods(dispatcher, memory_store)
    register_lsp_methods(dispatcher, lsp_manager)
    register_inline_methods(dispatcher, registry)
    register_terminal_methods(dispatcher, terminal_observer)
    register_browser_methods(dispatcher, browser_manager)
    register_mcp_methods(dispatcher, mcp_manager)
    register_email_methods(dispatcher, email_manager, credentials=email_credentials)
    # Eternal-thread surface (ADR-0022). Read methods plus the
    # write-side thread.send and the recovery-status helpers for the
    # in-progress recovery flow.
    register_thread_methods(dispatcher, threads_store)
    register_thread_send_methods(
        dispatcher,
        threads_store=threads_store,
        registry=registry,
        agent_records=agent_records_store,
    )

    # Auth-backend surface (ADR-0020). Real handlers replace the v2
    # ``auth.*`` stubs; the on-active-changed callback hot-swaps the
    # AnthropicProvider's auth backend so the next chat turn picks up
    # the user's selection without a brain restart.
    def _hot_swap_anthropic_auth(backend: AuthBackend) -> None:
        anthropic = registry.get("anthropic")
        if isinstance(anthropic, AnthropicProvider):
            anthropic.set_auth_backend(backend)

    register_auth_methods(
        dispatcher,
        auth_registry,
        on_active_changed=_hot_swap_anthropic_auth,
    )
    # Lead-lifecycle surface (ADR-0021). Real handlers replace the v2
    # ``lead.*`` stubs; the brain-side state machine owns the spawn /
    # pause / resume / archive transitions and keeps the project's
    # ``lead_agent_id`` pointer consistent.
    register_lead_methods(dispatcher, lead_lifecycle)
    # Stubs for the v2 IPC surface; real handlers replace these as
    # subsequent stages land per ADR-0021 / 02-architecture.md §6.
    register_v2_stubs(dispatcher)

    async def dispatch_schedule(schedule: Schedule) -> str | None:
        """Fire one schedule into the runner.

        Notifications fall through into a sink — the renderer doesn't
        observe scheduled runs the same way it does interactive
        ones; the runs index is the durable record.
        """
        run_template = schedule.run_template
        provider_id = run_template.get("providerId", "anthropic")
        prompt = run_template.get("prompt", "")
        if not isinstance(prompt, str) or not prompt:
            return None
        if not isinstance(provider_id, str):
            return None

        async def sink(_method: str, _params: object) -> None:
            return None

        try:
            result = await runner.run(
                session_id=f"schedule-{schedule.schedule_id}",
                provider_id=provider_id,
                prompt=prompt,
                notify=sink,
            )
        except Exception:
            return None
        return result.run_id

    scheduler = SchedulerLoop(schedules_store, dispatch_schedule)

    async def serve() -> None:
        # Pick up any runs that were in flight when the brain last
        # exited before opening the stdio surface to new traffic.
        await resume_unfinished_runs(runs_store, runner)
        scheduler.start()
        try:
            await serve_stdio(dispatcher)
        finally:
            await scheduler.stop()
            await lsp_manager.shutdown()
            await mcp_manager.shutdown()
            await email_manager.shutdown()

    try:
        asyncio.run(serve())
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
