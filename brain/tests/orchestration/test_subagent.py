"""Sub-agent spawn / observe / kill lifecycle tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from thalyn_brain.orchestration import Runner
from thalyn_brain.orchestration.state import RunStatus
from thalyn_brain.provider import AnthropicProvider, ProviderRegistry
from thalyn_brain.runs import RunsStore

from tests.provider._fake_sdk import factory_for, result_message, text_message


def _registry_with(provider: AnthropicProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry._providers["anthropic"] = provider
    return registry


def _captured() -> tuple[list[tuple[str, Any]], Any]:
    captured: list[tuple[str, Any]] = []

    async def notify(method: str, params: Any) -> None:
        captured.append((method, params))

    return captured, notify


def _plan_with_subagent(*, kind: str = "research") -> str:
    """A planner JSON payload whose single step is delegated."""
    return (
        '{"goal": "look stuff up",'
        ' "steps": ['
        '   {"description": "Investigate prior art.",'
        '    "rationale": "Need context.",'
        '    "estimated_tokens": 200,'
        f'    "subagent_kind": "{kind}"}}'
        "]}"
    )


async def test_spawn_writes_child_run_with_parent_link(tmp_path: Path) -> None:
    """Plan with a delegated step → child RunHeader exists, has the
    parent's run_id, and the parent ran end-to-end."""
    _fake, factory = factory_for(
        [
            # Parent: plan turn (delegates one step).
            text_message(_plan_with_subagent()),
            result_message(),
            # Sub-agent: plan turn — falls back to single-step plan.
            text_message('{"goal": "x", "steps": []}'),
            result_message(),
            # Sub-agent: respond turn.
            text_message("Investigated."),
            result_message(),
            # Parent: respond turn.
            text_message("Here is what I found."),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    store = RunsStore(data_dir=tmp_path)
    runner = Runner(registry, runs_store=store, data_dir=tmp_path)

    _, notify = _captured()
    paused = await runner.run(
        session_id="sess",
        provider_id="anthropic",
        prompt="Investigate.",
        notify=notify,
    )
    assert paused.status == RunStatus.AWAITING_APPROVAL.value
    parent = await runner.approve_plan(
        run_id=paused.run_id,
        provider_id="anthropic",
        decision="approve",
        notify=notify,
    )
    assert parent is not None
    assert parent.status == RunStatus.COMPLETED.value

    headers = await store.list_runs()
    parent_header = next(h for h in headers if h.run_id == parent.run_id)
    children = [h for h in headers if h.parent_run_id == parent.run_id]
    assert parent_header.parent_run_id is None
    assert len(children) == 1
    child = children[0]
    assert child.status == RunStatus.COMPLETED.value
    # The child's title falls out of the plan node's description.
    assert child.title == "Investigate prior art."


async def test_spawn_emits_child_status_notifications(tmp_path: Path) -> None:
    """The child's lifecycle surfaces under its own runId so the
    renderer can route events to its tile."""
    _fake, factory = factory_for(
        [
            text_message(_plan_with_subagent()),
            result_message(),
            text_message('{"goal": "x", "steps": []}'),
            result_message(),
            text_message("ok."),
            result_message(),
            text_message("done."),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    store = RunsStore(data_dir=tmp_path)
    runner = Runner(registry, runs_store=store, data_dir=tmp_path)

    captured, notify = _captured()
    paused = await runner.run(
        session_id="s",
        provider_id="anthropic",
        prompt="Hello",
        notify=notify,
    )
    parent = await runner.approve_plan(
        run_id=paused.run_id,
        provider_id="anthropic",
        decision="approve",
        notify=notify,
    )
    assert parent is not None

    parent_run_id = parent.run_id
    child_run_ids = {
        params["runId"]
        for method, params in captured
        if method == "run.status" and params["runId"] != parent_run_id
    }
    assert len(child_run_ids) == 1
    child_run_id = next(iter(child_run_ids))

    child_statuses = [
        params["status"]
        for method, params in captured
        if method == "run.status" and params["runId"] == child_run_id
    ]
    assert child_statuses[0] == RunStatus.PENDING.value
    assert child_statuses[-1] == RunStatus.COMPLETED.value


async def test_inline_step_does_not_spawn(tmp_path: Path) -> None:
    """A plan without subagent_kind keeps the v0.5 inline behaviour —
    no child RunHeader is created."""
    _fake, factory = factory_for(
        [
            text_message(
                '{"goal": "x",'
                ' "steps": [{"description": "Answer.",'
                ' "rationale": "Trivial.", "estimated_tokens": 100}]}'
            ),
            result_message(),
            text_message("Here you go."),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    store = RunsStore(data_dir=tmp_path)
    runner = Runner(registry, runs_store=store, data_dir=tmp_path)

    _, notify = _captured()
    paused = await runner.run(
        session_id="s",
        provider_id="anthropic",
        prompt="Hi",
        notify=notify,
    )
    parent = await runner.approve_plan(
        run_id=paused.run_id,
        provider_id="anthropic",
        decision="approve",
        notify=notify,
    )
    assert parent is not None

    headers = await store.list_runs()
    children = [h for h in headers if h.parent_run_id == parent.run_id]
    assert children == []


async def test_kill_run_marks_killed_in_index_and_emits_status(
    tmp_path: Path,
) -> None:
    """``kill_run`` flips the persistent state and announces the
    transition without forcibly cancelling in-flight asyncio work."""
    _fake, factory = factory_for(
        [
            text_message('{"goal": "x", "steps": []}'),
            result_message(),
            text_message("ok."),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    store = RunsStore(data_dir=tmp_path)
    runner = Runner(registry, runs_store=store, data_dir=tmp_path)

    _, send_notify = _captured()
    paused = await runner.run(
        session_id="s",
        provider_id="anthropic",
        prompt="Hello",
        notify=send_notify,
    )

    captured, notify = _captured()
    killed = await runner.kill_run(run_id=paused.run_id, notify=notify)
    assert killed is not None
    assert killed.status == RunStatus.KILLED.value

    statuses = [
        params["status"]
        for method, params in captured
        if method == "run.status" and params["runId"] == paused.run_id
    ]
    assert statuses == [RunStatus.KILLED.value]

    header = await store.get(paused.run_id)
    assert header is not None
    assert header.status == RunStatus.KILLED.value
    assert header.completed_at_ms is not None


async def test_depth_cap_blocks_deep_spawn_and_emits_approval_gate(
    tmp_path: Path,
) -> None:
    """With a cap of 1, a delegated step at depth 0 spawns a child;
    the child's own delegated step would land at depth 2 and is
    blocked behind a depth-gate approval notification."""
    _fake, factory = factory_for(
        [
            # Root run plan: one delegated step.
            text_message(_plan_with_subagent()),
            result_message(),
            # Child run plan: also delegated — would push depth to 2.
            text_message(_plan_with_subagent(kind="edit")),
            result_message(),
            # Child's respond turn (after the depth-blocked spawn skips).
            text_message("blocked."),
            result_message(),
            # Root's respond turn.
            text_message("done."),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    store = RunsStore(data_dir=tmp_path)
    runner = Runner(
        registry,
        runs_store=store,
        data_dir=tmp_path,
        depth_cap=1,
    )

    captured, notify = _captured()
    paused = await runner.run(
        session_id="s",
        provider_id="anthropic",
        prompt="Do the deep thing.",
        notify=notify,
    )
    finished = await runner.approve_plan(
        run_id=paused.run_id,
        provider_id="anthropic",
        decision="approve",
        notify=notify,
    )
    assert finished is not None

    # Exactly one child landed in the runs index — the depth-blocked
    # grandchild never spawned.
    headers = await store.list_runs()
    children = [h for h in headers if h.parent_run_id == finished.run_id]
    grandchildren = [h for h in headers if h.parent_run_id == children[0].run_id]
    assert len(children) == 1
    assert grandchildren == []

    # A depth-gate approval notification fired against the child's
    # run id (it was the one trying to spawn).
    depth_gates = [
        params
        for method, params in captured
        if method == "run.approval_required" and params.get("gateKind") == "depth"
    ]
    assert len(depth_gates) == 1
    assert depth_gates[0]["depth"] == 2
    assert depth_gates[0]["depthCap"] == 1
    assert depth_gates[0]["runId"] == children[0].run_id


async def test_default_depth_cap_allows_two_levels(tmp_path: Path) -> None:
    """The default cap (2) lets a sub-agent of a sub-agent run to
    completion; the third level would be the one that's blocked."""
    _fake, factory = factory_for(
        [
            # Root plan: one delegated step.
            text_message(_plan_with_subagent()),
            result_message(),
            # Child plan: one more delegated step (depth 2 — allowed).
            text_message(_plan_with_subagent(kind="edit")),
            result_message(),
            # Grandchild plan: empty.
            text_message('{"goal": "x", "steps": []}'),
            result_message(),
            # Grandchild respond.
            text_message("grand."),
            result_message(),
            # Child respond.
            text_message("child."),
            result_message(),
            # Root respond.
            text_message("root."),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    store = RunsStore(data_dir=tmp_path)
    runner = Runner(registry, runs_store=store, data_dir=tmp_path)

    _, notify = _captured()
    paused = await runner.run(
        session_id="s",
        provider_id="anthropic",
        prompt="Hello",
        notify=notify,
    )
    finished = await runner.approve_plan(
        run_id=paused.run_id,
        provider_id="anthropic",
        decision="approve",
        notify=notify,
    )
    assert finished is not None

    headers = await store.list_runs()
    children = [h for h in headers if h.parent_run_id == finished.run_id]
    assert len(children) == 1
    grandchildren = [h for h in headers if h.parent_run_id == children[0].run_id]
    assert len(grandchildren) == 1
    assert grandchildren[0].status == RunStatus.COMPLETED.value


async def test_subagent_kind_round_trips_through_planner(tmp_path: Path) -> None:
    """Planner JSON ``subagent_kind`` lands on the wire as
    ``subagentKind`` so the UI and execute node both see it."""
    _fake, factory = factory_for(
        [
            text_message(_plan_with_subagent(kind="edit")),
            result_message(),
            text_message('{"goal": "x", "steps": []}'),
            result_message(),
            text_message("done."),
            result_message(),
            text_message("Done."),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    store = RunsStore(data_dir=tmp_path)
    runner = Runner(registry, runs_store=store, data_dir=tmp_path)

    captured, notify = _captured()
    paused = await runner.run(
        session_id="s",
        provider_id="anthropic",
        prompt="Edit a file",
        notify=notify,
    )
    plan_updates = [params for method, params in captured if method == "run.plan_update"]
    assert plan_updates
    first_node = plan_updates[0]["plan"]["nodes"][0]
    assert first_node["subagentKind"] == "edit"

    await runner.approve_plan(
        run_id=paused.run_id,
        provider_id="anthropic",
        decision="approve",
        notify=notify,
    )
