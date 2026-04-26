"""Memory persistence across sessions + integration with chat.send."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from thalyn_brain.chat import register_chat_methods
from thalyn_brain.memory import MemoryStore
from thalyn_brain.memory_writes import record_memory_write
from thalyn_brain.orchestration import Runner
from thalyn_brain.provider import AnthropicProvider, ProviderRegistry
from thalyn_brain.rpc import Dispatcher

from tests.provider._fake_sdk import factory_for, result_message, text_message


def _registry_with(provider: AnthropicProvider) -> ProviderRegistry:
    registry = ProviderRegistry()
    registry._providers["anthropic"] = provider
    return registry


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


async def test_memory_survives_a_fresh_store_instance(tmp_path: Path) -> None:
    """Drop the MemoryStore reference, create a new one against the
    same data dir, and confirm entries persist — the cross-session
    contract from F8.x."""
    store_a = MemoryStore(data_dir=tmp_path)
    await record_memory_write(
        store_a,
        run_id="r_1",
        body="User prefers tabs over spaces.",
        scope="user",
        kind="preference",
        author="agent",
    )
    await record_memory_write(
        store_a,
        run_id="r_1",
        body="Reviewer wants Conventional Commits.",
        scope="project",
        kind="reference",
        author="user",
    )
    del store_a

    store_b = MemoryStore(data_dir=tmp_path)
    entries = await store_b.list_entries()
    bodies = {entry.body for entry in entries}
    assert "User prefers tabs over spaces." in bodies
    assert "Reviewer wants Conventional Commits." in bodies


async def test_memory_search_still_works_after_restart(tmp_path: Path) -> None:
    store_a = MemoryStore(data_dir=tmp_path)
    await record_memory_write(
        store_a,
        run_id="r_x",
        body="The user's preferred indentation is tabs.",
        scope="user",
        kind="preference",
        author="agent",
    )
    del store_a

    store_b = MemoryStore(data_dir=tmp_path)
    hits = await store_b.search("tabs")
    assert len(hits) == 1
    assert "tabs" in hits[0].body


# ---------------------------------------------------------------------------
# chat.send picks up the project context file
# ---------------------------------------------------------------------------


async def test_chat_send_loads_thalyn_md_into_run(tmp_path: Path) -> None:
    """When chat.send carries a workspaceRoot containing THALYN.md,
    the response body's projectContext echoes the loaded file."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "THALYN.md").write_text(
        "Project conventions:\n- Use tabs.\n- Conventional Commits required.\n"
    )

    _fake, factory = factory_for(
        [
            text_message('{"goal": "x", "steps": []}'),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    runner = Runner(registry, data_dir=tmp_path)
    dispatcher = Dispatcher()
    register_chat_methods(dispatcher, registry, runner=runner)

    async def notify(method: str, params: Any) -> None:
        del method, params

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "chat.send",
            "params": {
                "sessionId": "sess",
                "providerId": "anthropic",
                "prompt": "Hello",
                "workspaceRoot": str(workspace),
            },
        },
        notify,
    )
    assert response is not None
    project_context = response["result"]["projectContext"]
    assert project_context["sourceFilename"] == "THALYN.md"
    assert "Use tabs." in project_context["body"]
    assert project_context["truncated"] is False


async def test_chat_send_without_workspace_root_skips_project_context(
    tmp_path: Path,
) -> None:
    _fake, factory = factory_for(
        [
            text_message('{"goal": "x", "steps": []}'),
            result_message(),
        ]
    )
    provider = AnthropicProvider(client_factory=factory)
    registry = _registry_with(provider)
    runner = Runner(registry, data_dir=tmp_path)
    dispatcher = Dispatcher()
    register_chat_methods(dispatcher, registry, runner=runner)

    async def notify(method: str, params: Any) -> None:
        del method, params

    response = await dispatcher.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "chat.send",
            "params": {
                "sessionId": "sess",
                "providerId": "anthropic",
                "prompt": "Hello",
            },
        },
        notify,
    )
    assert response is not None
    assert "projectContext" not in response["result"]
