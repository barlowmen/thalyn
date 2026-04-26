"""Durable resumption — survive a "kill" by abandoning one Runner
and constructing a fresh one against the same data dir.

LangGraph's SqliteSaver checkpoints every node transition; the
``RunsStore`` persists header rows. On restart, ``resume_unfinished_runs``
walks the index and replays each one. The tests here simulate the
"process died" case by dropping the original Runner reference and
spinning up a new one — the persistent state is the only carrier
that crosses the boundary.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from thalyn_brain.orchestration import Runner
from thalyn_brain.orchestration.resume import resume_unfinished_runs
from thalyn_brain.orchestration.state import RunStatus
from thalyn_brain.provider import AnthropicProvider, ProviderRegistry
from thalyn_brain.runs import RunsStore

from tests.provider._fake_sdk import factory_for, result_message, text_message


def _registry_with(provider: AnthropicProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry._providers["anthropic"] = provider
    return registry


def _silent() -> Any:
    async def notify(method: str, params: Any) -> None:
        del method, params

    return notify


async def test_resume_after_pause_preserves_awaiting_approval(tmp_path: Path) -> None:
    """A run that paused at the plan-approval interrupt before the
    first Runner died should still be `awaiting_approval` after a
    fresh Runner picks it up via `resume_unfinished_runs`."""
    _fake, factory = factory_for(
        [
            text_message('{"goal": "x", "steps": []}'),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)

    # First Runner — drives the run to the plan-approval gate, then
    # we drop the reference to simulate process death.
    store = RunsStore(data_dir=tmp_path)
    runner = Runner(registry, runs_store=store, data_dir=tmp_path)
    paused = await runner.run(
        session_id="sess",
        provider_id="anthropic",
        prompt="Hello",
        notify=_silent(),
    )
    assert paused.status == RunStatus.AWAITING_APPROVAL.value
    run_id = paused.run_id
    del runner

    # Second Runner — fresh process simulation. The runs index +
    # the per-run checkpoint db are the only state that crosses.
    store_2 = RunsStore(data_dir=tmp_path)
    runner_2 = Runner(registry, runs_store=store_2, data_dir=tmp_path)
    touched = await resume_unfinished_runs(store_2, runner_2)
    assert run_id in touched

    header = await store_2.get(run_id)
    assert header is not None
    assert header.status == RunStatus.AWAITING_APPROVAL.value


async def test_resume_completes_a_run_paused_just_before_respond(tmp_path: Path) -> None:
    """A run already approved before the kill resumes through respond
    when the new Runner picks it up. The respond turn's FakeClient
    fixtures are seeded on the second Runner so the resume can
    complete the work."""
    plan_factory = factory_for(
        [
            text_message('{"goal": "x", "steps": []}'),
            result_message(),
        ]
    )
    provider_1 = AnthropicProvider(client_factory=plan_factory[1])
    registry_1 = _registry_with(provider_1)

    store = RunsStore(data_dir=tmp_path)
    runner_1 = Runner(registry_1, runs_store=store, data_dir=tmp_path)
    paused = await runner_1.run(
        session_id="sess",
        provider_id="anthropic",
        prompt="Hello",
        notify=_silent(),
    )
    run_id = paused.run_id

    # The approve_plan call resumes the graph; we fake the kill by
    # building a new Runner against the same data dir to drive the
    # approve. Approving the run drives execute → critic → respond,
    # so the second Runner needs respond fixtures only — LangGraph's
    # checkpoint short-circuits the planner replay.
    respond_factory = factory_for(
        [
            text_message("done."),
            result_message(),
        ]
    )
    provider_2 = AnthropicProvider(client_factory=respond_factory[1])
    registry_2 = _registry_with(provider_2)
    runner_2 = Runner(registry_2, runs_store=store, data_dir=tmp_path)
    finished = await runner_2.approve_plan(
        run_id=run_id,
        provider_id="anthropic",
        decision="approve",
        notify=_silent(),
    )
    assert finished is not None
    assert finished.status == RunStatus.COMPLETED.value

    header = await store.get(run_id)
    assert header is not None
    assert header.status == RunStatus.COMPLETED.value
    assert header.final_response == "done."


async def test_resume_marks_run_errored_when_no_checkpoint_exists(tmp_path: Path) -> None:
    """A header that points at a run with no per-run checkpoint db
    can't resume; the recovery path marks it errored so it disappears
    from the unfinished-runs list."""
    store = RunsStore(data_dir=tmp_path)
    from thalyn_brain.runs import RunHeader

    await store.insert(
        RunHeader(
            run_id="r_orphan",
            project_id=None,
            parent_run_id=None,
            status=RunStatus.RUNNING.value,
            title="orphan",
            provider_id="anthropic",
            started_at_ms=1,
            completed_at_ms=None,
            drift_score=0.0,
            final_response="",
        )
    )

    _fake, factory = factory_for([])
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    runner = Runner(registry, runs_store=store, data_dir=tmp_path)

    touched = await resume_unfinished_runs(store, runner)
    assert "r_orphan" in touched

    header = await store.get("r_orphan")
    assert header is not None
    assert header.status == RunStatus.ERRORED.value
    assert "no checkpoint" in header.final_response
