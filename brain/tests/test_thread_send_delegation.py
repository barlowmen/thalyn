"""End-to-end tests for the brain → lead delegation flow inside
``thread.send``.

These build the full dispatcher (provider registry + threads store +
agent registry + lead lifecycle) and drive a single ``thread.send``
call that addresses a lead by name. The assertions cover the
contract this phase ships:

- The user turn lands as ``user`` and stays addressable.
- A new ``role='lead'`` turn captures the lead's raw reply, with
  ``agent_id`` set to the lead.
- The brain's reply turn carries ``provenance.delegatedTo`` /
  ``leadTurnId`` so the renderer can drill into the source (F1.10).
- The thread.chunk stream surfaces the brain's preamble + the
  ``"<name> says: …"`` wrap and a final stop chunk.
- Switching the lead to paused turns delegation off (the brain
  replies directly).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from thalyn_brain.agents import AgentRecordsStore
from thalyn_brain.lead_lifecycle import LeadLifecycle, SpawnRequest, SubLeadSpawnRequest
from thalyn_brain.projects import Project, ProjectsStore, new_project_id
from thalyn_brain.provider import AnthropicProvider, ProviderRegistry
from thalyn_brain.rpc import Dispatcher
from thalyn_brain.thread_send import register_thread_send_methods
from thalyn_brain.threads import Thread, ThreadsStore, new_thread_id

from tests.provider._fake_sdk import factory_for, result_message, text_message


def _now() -> int:
    return int(time.time() * 1000)


def _registry_with(provider: AnthropicProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry._providers["anthropic"] = provider
    return registry


def _captured_notifier() -> tuple[list[tuple[str, Any]], Any]:
    captured: list[tuple[str, Any]] = []

    async def notify(method: str, params: Any) -> None:
        captured.append((method, params))

    return captured, notify


async def _seed_thread(store: ThreadsStore) -> Thread:
    thread = Thread(
        thread_id=new_thread_id(),
        user_scope="self",
        created_at_ms=_now(),
        last_active_at_ms=_now(),
    )
    await store.insert_thread(thread)
    return thread


async def _seed_project(projects: ProjectsStore) -> Project:
    project = Project(
        project_id=new_project_id(),
        name="Alpha",
        slug="alpha",
        workspace_path=None,
        repo_remote=None,
        lead_agent_id=None,
        memory_namespace="alpha",
        conversation_tag="Alpha",
        roadmap="",
        provider_config=None,
        connector_grants=None,
        local_only=False,
        status="active",
        created_at_ms=_now(),
        last_active_at_ms=_now(),
    )
    await projects.insert(project)
    return project


async def _build(
    tmp_path: Path,
    *,
    messages: list[Any],
) -> tuple[Dispatcher, ThreadsStore, AgentRecordsStore, LeadLifecycle, ProjectsStore]:
    threads = ThreadsStore(data_dir=tmp_path)
    agents = AgentRecordsStore(data_dir=tmp_path)
    projects = ProjectsStore(data_dir=tmp_path)
    lifecycle = LeadLifecycle(agents=agents, projects=projects)

    _, factory = factory_for(messages)
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)

    dispatcher = Dispatcher()
    register_thread_send_methods(
        dispatcher,
        threads_store=threads,
        registry=registry,
        agent_records=agents,
    )
    return dispatcher, threads, agents, lifecycle, projects


async def _send(
    dispatcher: Dispatcher,
    *,
    thread_id: str,
    prompt: str,
    request_id: int = 1,
) -> tuple[dict[str, Any], list[tuple[str, Any]]]:
    captured, notify = _captured_notifier()
    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "thread.send",
            "params": {
                "threadId": thread_id,
                "providerId": "anthropic",
                "prompt": prompt,
            },
        },
        notify,
    )
    assert response is not None
    return response, captured


async def test_addressed_lead_runs_delegation_and_persists_lead_turn(tmp_path: Path) -> None:
    dispatcher, threads, _agents, lifecycle, projects = await _build(
        tmp_path,
        messages=[
            text_message("3 commits shipped overnight; 1 question pending."),
            result_message(),
        ],
    )
    project = await _seed_project(projects)
    lead = await lifecycle.spawn(
        SpawnRequest(project_id=project.project_id, display_name="Sam"),
    )
    thread = await _seed_thread(threads)

    response, captured = await _send(
        dispatcher,
        thread_id=thread.thread_id,
        prompt="Sam, status on the auth refactor?",
    )

    result = response["result"]
    assert result["status"] == "completed"
    assert result["delegation"]["leadId"] == lead.agent_id
    assert result["delegation"]["leadDisplayName"] == "Sam"
    confidence = result["delegation"]["confidence"]
    assert confidence["level"] == "high"
    assert confidence["audit"]["mode"] == "reported_vs_truth"

    # Three turns landed: user, lead, brain.
    turns = await threads.list_turns(thread.thread_id)
    assert [t.role for t in turns] == ["user", "lead", "brain"]
    assert turns[1].agent_id == lead.agent_id
    assert turns[1].body == "3 commits shipped overnight; 1 question pending."
    assert turns[2].agent_id == "agent_brain"
    assert turns[2].provenance is not None
    assert turns[2].provenance["delegatedTo"] == lead.agent_id
    assert turns[2].provenance["leadTurnId"] == turns[1].turn_id
    # The brain's surfaced body wraps the lead reply.
    assert "Sam says:" in turns[2].body
    assert "Asking Sam now" in turns[2].body

    # The chunk stream went out under the brain's turn id with a
    # start, at least one text delta, and a stop chunk.
    chunks = [params for method, params in captured if method == "thread.chunk"]
    assert chunks, "delegation flow emitted no thread.chunk events"
    assert all(ev["turnId"] == result["turnId"] for ev in chunks)
    kinds = [ev["chunk"]["kind"] for ev in chunks]
    assert kinds[0] == "start"
    assert "text" in kinds
    assert kinds[-1] == "stop"


async def test_unaddressed_message_falls_back_to_direct_reply(tmp_path: Path) -> None:
    dispatcher, threads, _agents, lifecycle, projects = await _build(
        tmp_path,
        messages=[text_message("brain reply"), result_message()],
    )
    project = await _seed_project(projects)
    await lifecycle.spawn(SpawnRequest(project_id=project.project_id, display_name="Sam"))
    thread = await _seed_thread(threads)

    response, _ = await _send(
        dispatcher,
        thread_id=thread.thread_id,
        prompt="how's the build going?",
    )

    result = response["result"]
    assert "delegation" not in result
    turns = await threads.list_turns(thread.thread_id)
    assert [t.role for t in turns] == ["user", "brain"]
    assert turns[1].body == "brain reply"


async def test_paused_lead_does_not_attract_delegation(tmp_path: Path) -> None:
    dispatcher, threads, _agents, lifecycle, projects = await _build(
        tmp_path,
        messages=[text_message("direct reply"), result_message()],
    )
    project = await _seed_project(projects)
    lead = await lifecycle.spawn(
        SpawnRequest(project_id=project.project_id, display_name="Sam"),
    )
    await lifecycle.pause(lead.agent_id)
    thread = await _seed_thread(threads)

    response, _ = await _send(
        dispatcher,
        thread_id=thread.thread_id,
        prompt="Sam, status?",
    )

    result = response["result"]
    # No delegation key — paused lead, brain replies directly.
    assert "delegation" not in result
    turns = await threads.list_turns(thread.thread_id)
    assert [t.role for t in turns] == ["user", "brain"]


async def test_at_mention_mid_message_routes_to_lead(tmp_path: Path) -> None:
    """``@<lead-name>`` anywhere in the message routes to the lead.

    The mid-message form preserves the surrounding sentence — the
    lead sees the full body, not the leading-address-stripped
    suffix the start-of-message form returns.
    """
    dispatcher, threads, _agents, lifecycle, projects = await _build(
        tmp_path,
        messages=[text_message("here's the rundown."), result_message()],
    )
    project = await _seed_project(projects)
    lead = await lifecycle.spawn(
        SpawnRequest(project_id=project.project_id, display_name="Sam"),
    )
    thread = await _seed_thread(threads)

    response, _ = await _send(
        dispatcher,
        thread_id=thread.thread_id,
        prompt="hey @Sam, can you sum up the auth refactor?",
    )

    result = response["result"]
    assert result["delegation"]["leadId"] == lead.agent_id
    turns = await threads.list_turns(thread.thread_id)
    assert [t.role for t in turns] == ["user", "lead", "brain"]
    # The user-turn body still carries the original mention so the
    # transcript reads naturally on re-render.
    assert turns[0].body == "hey @Sam, can you sum up the auth refactor?"


async def test_hedged_lead_reply_surfaces_low_confidence_note(tmp_path: Path) -> None:
    dispatcher, threads, _agents, lifecycle, projects = await _build(
        tmp_path,
        messages=[text_message("I'm not sure about that."), result_message()],
    )
    project = await _seed_project(projects)
    await lifecycle.spawn(SpawnRequest(project_id=project.project_id, display_name="Sam"))
    thread = await _seed_thread(threads)

    response, _ = await _send(
        dispatcher,
        thread_id=thread.thread_id,
        prompt="Sam, what's the auth state?",
    )

    result = response["result"]
    confidence = result["delegation"]["confidence"]
    # Leading hedge phrase produces mid-band drift → medium confidence
    # (the pill surfaces, the gate does not).
    assert confidence["level"] == "medium"
    assert confidence["audit"]["mode"] == "reported_vs_truth"
    assert confidence["audit"]["driftScore"] > 0.3
    turns = await threads.list_turns(thread.thread_id)
    assert "Low-confidence" in turns[2].body


async def test_addressed_lead_audit_emits_run_drift_with_mode(tmp_path: Path) -> None:
    """Every lead-delegation hop emits a ``run.drift`` notification
    carrying the new ``mode`` field, regardless of whether the audit
    flagged drift. The audit fact is part of the run record, not only
    the flagging."""
    dispatcher, threads, _agents, lifecycle, projects = await _build(
        tmp_path,
        messages=[
            text_message("3 commits shipped overnight."),
            result_message(),
        ],
    )
    project = await _seed_project(projects)
    await lifecycle.spawn(SpawnRequest(project_id=project.project_id, display_name="Sam"))
    thread = await _seed_thread(threads)

    response, captured = await _send(
        dispatcher,
        thread_id=thread.thread_id,
        prompt="Sam, status?",
    )

    drift_events = [params for method, params in captured if method == "run.drift"]
    # Both hops emit drift events (reported_vs_truth + relayed_vs_source).
    assert [ev["mode"] for ev in drift_events] == [
        "reported_vs_truth",
        "relayed_vs_source",
    ]
    assert all(ev["runId"].startswith("chat:") for ev in drift_events)
    # No flagging on a clean reply → no info_flow gate.
    gates = [
        params
        for method, params in captured
        if method == "run.approval_required" and params.get("gateKind") == "info_flow"
    ]
    assert gates == []
    # The audit facts still land in the action log — one per hop.
    log_entries = [
        params
        for method, params in captured
        if method == "run.action_log" and params["entry"]["kind"] == "info_flow_check"
    ]
    assert [entry["entry"]["payload"]["mode"] for entry in log_entries] == [
        "reported_vs_truth",
        "relayed_vs_source",
    ]
    assert response["result"]["delegation"]["confidence"]["level"] == "high"


async def test_relay_audit_also_emits_run_drift(tmp_path: Path) -> None:
    """The brain → user hop emits its own ``run.drift`` notification
    with ``mode='relayed_vs_source'`` alongside the
    ``reported_vs_truth`` audit. The verbatim v1 wrap is faithful, so
    the relay audit normally scores 0 — the assertion is that the
    audit fact lands on every relay, not that drift fires."""
    dispatcher, threads, _agents, lifecycle, projects = await _build(
        tmp_path,
        messages=[
            text_message("Migration succeeded; 71 tests pass."),
            result_message(),
        ],
    )
    project = await _seed_project(projects)
    await lifecycle.spawn(SpawnRequest(project_id=project.project_id, display_name="Sam"))
    thread = await _seed_thread(threads)

    _response, captured = await _send(
        dispatcher,
        thread_id=thread.thread_id,
        prompt="Sam, status?",
    )

    drift_events = [params for method, params in captured if method == "run.drift"]
    modes = {ev["mode"] for ev in drift_events}
    assert modes == {"reported_vs_truth", "relayed_vs_source"}
    log_entries = [
        params
        for method, params in captured
        if method == "run.action_log" and params["entry"]["kind"] == "info_flow_check"
    ]
    log_modes = {entry["entry"]["payload"]["mode"] for entry in log_entries}
    assert log_modes == {"reported_vs_truth", "relayed_vs_source"}


async def test_empty_lead_reply_raises_info_flow_gate(tmp_path: Path) -> None:
    """A lead replying with whitespace is the canonical
    "reports X but the action log shows Y" failure mode: the report
    is empty, the audit flags drift at 1.0, and the runtime surfaces
    the ``info_flow`` approval gate."""
    dispatcher, threads, _agents, lifecycle, projects = await _build(
        tmp_path,
        messages=[text_message("   "), result_message()],
    )
    project = await _seed_project(projects)
    await lifecycle.spawn(SpawnRequest(project_id=project.project_id, display_name="Sam"))
    thread = await _seed_thread(threads)

    response, captured = await _send(
        dispatcher,
        thread_id=thread.thread_id,
        prompt="Sam, status?",
    )

    gates = [
        params
        for method, params in captured
        if method == "run.approval_required" and params.get("gateKind") == "info_flow"
    ]
    assert len(gates) == 1
    summary = gates[0]["infoFlowSummary"]
    assert summary["mode"] == "reported_vs_truth"
    assert summary["driftScore"] == 1.0
    assert summary["sourceRef"]["leadId"]
    assert summary["outputRef"]["turnId"] == response["result"]["turnId"]

    confidence = response["result"]["delegation"]["confidence"]
    assert confidence["level"] == "low"


async def test_lead_session_loads_thalyn_md_into_system_prompt(tmp_path: Path) -> None:
    """When the project has a workspace_path and a ``THALYN.md`` lives
    there, the lead's provider sees the file's contents merged in
    front of its identity prompt — the F6.3 project-memory tier is
    auto-loaded at session start without the user re-pasting it."""
    workspace = tmp_path / "alpha-workspace"
    workspace.mkdir()
    (workspace / "THALYN.md").write_text(
        "# Alpha conventions\n- Always run pnpm lint before pushing.\n",
    )

    threads = ThreadsStore(data_dir=tmp_path)
    agents = AgentRecordsStore(data_dir=tmp_path)
    projects = ProjectsStore(data_dir=tmp_path)
    lifecycle = LeadLifecycle(agents=agents, projects=projects)

    fake, factory = factory_for([text_message("noted"), result_message()])
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    dispatcher = Dispatcher()
    register_thread_send_methods(
        dispatcher,
        threads_store=threads,
        registry=registry,
        agent_records=agents,
        projects_store=projects,
    )

    project = Project(
        project_id=new_project_id(),
        name="Alpha",
        slug="alpha",
        workspace_path=str(workspace),
        repo_remote=None,
        lead_agent_id=None,
        memory_namespace="alpha",
        conversation_tag="Alpha",
        roadmap="",
        provider_config=None,
        connector_grants=None,
        local_only=False,
        status="active",
        created_at_ms=_now(),
        last_active_at_ms=_now(),
    )
    await projects.insert(project)
    await lifecycle.spawn(SpawnRequest(project_id=project.project_id, display_name="Sam"))
    thread = await _seed_thread(threads)

    await _send(
        dispatcher,
        thread_id=thread.thread_id,
        prompt="Sam, what should I check before pushing?",
    )

    sys_prompt = fake.options.system_prompt if fake.options is not None else ""
    assert sys_prompt is not None
    assert "Project context — THALYN.md" in sys_prompt
    assert "Always run pnpm lint" in sys_prompt


async def test_lead_session_without_workspace_path_skips_project_context(
    tmp_path: Path,
) -> None:
    """A project without a workspace_path doesn't try to load
    ``THALYN.md`` and the lead's prompt stays at the identity
    template."""
    threads = ThreadsStore(data_dir=tmp_path)
    agents = AgentRecordsStore(data_dir=tmp_path)
    projects = ProjectsStore(data_dir=tmp_path)
    lifecycle = LeadLifecycle(agents=agents, projects=projects)

    fake, factory = factory_for([text_message("noted"), result_message()])
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    dispatcher = Dispatcher()
    register_thread_send_methods(
        dispatcher,
        threads_store=threads,
        registry=registry,
        agent_records=agents,
        projects_store=projects,
    )

    project = await _seed_project(projects)  # workspace_path=None
    await lifecycle.spawn(SpawnRequest(project_id=project.project_id, display_name="Sam"))
    thread = await _seed_thread(threads)

    await _send(
        dispatcher,
        thread_id=thread.thread_id,
        prompt="Sam, ping?",
    )

    sys_prompt = fake.options.system_prompt if fake.options is not None else ""
    assert sys_prompt is not None
    assert "Project context" not in sys_prompt


async def test_addressed_sub_lead_routes_through_attribution_chain(
    tmp_path: Path,
) -> None:
    """A sub-lead can be addressed by name; the brain renders the
    attribution chain in both the surface text and the brain turn's
    provenance.

    The chain is what F2.3 surfaces back to the user — they need to
    see the message came through Lead-Alpha → SubLead-UI rather than
    a bare ``"SubLead-UI says: ..."`` that hides the parent.
    """
    dispatcher, threads, _agents, lifecycle, projects = await _build(
        tmp_path,
        messages=[
            text_message("UI bench is back at p99 < 50ms."),
            result_message(),
        ],
    )
    project = await _seed_project(projects)
    lead = await lifecycle.spawn(
        SpawnRequest(project_id=project.project_id, display_name="Lead-Alpha"),
    )
    sub = await lifecycle.spawn_sub_lead(
        SubLeadSpawnRequest(
            parent_agent_id=lead.agent_id,
            scope_facet="ui",
            display_name="SubLead-UI",
        ),
    )
    thread = await _seed_thread(threads)

    response, _ = await _send(
        dispatcher,
        thread_id=thread.thread_id,
        prompt="SubLead-UI, where are we on the bench?",
    )

    result = response["result"]
    delegation = result["delegation"]
    assert delegation["leadId"] == sub.agent_id
    assert delegation["leadDisplayName"] == "SubLead-UI"
    assert delegation["parentLeadId"] == lead.agent_id
    assert delegation["parentLeadDisplayName"] == "Lead-Alpha"
    chain = delegation["attributionChain"]
    assert chain["names"] == ["Thalyn", "Lead-Alpha", "SubLead-UI"]
    assert chain["agentIds"] == ["agent_brain", lead.agent_id, sub.agent_id]

    turns = await threads.list_turns(thread.thread_id)
    assert [t.role for t in turns] == ["user", "lead", "brain"]
    # The wrapped reply names the parent so the user sees the chain
    # without drilling into provenance.
    assert "SubLead-UI (under Lead-Alpha) says:" in turns[2].body
    # The lead turn's provenance carries the parent pointer for the
    # drill-down.
    assert turns[1].provenance is not None
    assert turns[1].provenance["parentLeadId"] == lead.agent_id
    assert turns[2].provenance is not None
    assert turns[2].provenance["attributionChain"]["names"] == [
        "Thalyn",
        "Lead-Alpha",
        "SubLead-UI",
    ]


async def test_delegation_disabled_when_no_agent_records_store(tmp_path: Path) -> None:
    """The optional ``agent_records`` parameter keeps callers (and
    tests) that don't wire the registry on the v0.21 fast path."""
    threads = ThreadsStore(data_dir=tmp_path)
    _, factory = factory_for([text_message("direct"), result_message()])
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    dispatcher = Dispatcher()
    register_thread_send_methods(
        dispatcher,
        threads_store=threads,
        registry=registry,
        # agent_records intentionally omitted
    )
    thread = await _seed_thread(threads)

    response, _ = await _send(
        dispatcher,
        thread_id=thread.thread_id,
        prompt="Sam, status?",
    )

    assert "delegation" not in response["result"]
