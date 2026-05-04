"""Tests for the ``voice.*`` JSON-RPC surface (v0.33 seam).

The brain's voice role is narrow: serve the project-vocabulary slice
the Rust core threads into Whisper's ``initial_prompt``. This test
asserts the wire shape of ``voice.project_vocabulary`` so the later
real-vocabulary commit lands without changing what the Rust core
parses.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from thalyn_brain.projects import ProjectsStore
from thalyn_brain.rpc import Dispatcher
from thalyn_brain.voice_rpc import register_voice_methods


class _DropNotify:
    async def __call__(self, method: str, params: Any) -> None:
        return None


_drop_notify = _DropNotify()


def _setup(tmp_path: Path) -> Dispatcher:
    projects = ProjectsStore(data_dir=tmp_path)
    dispatcher = Dispatcher()
    register_voice_methods(dispatcher, projects=projects)
    return dispatcher


async def _call(
    dispatcher: Dispatcher,
    method: str,
    params: dict[str, Any],
) -> dict[str, Any]:
    response = await dispatcher.handle(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        notify=_drop_notify,
    )
    assert response is not None
    return response


async def test_project_vocabulary_returns_terms_array(tmp_path: Path) -> None:
    dispatcher = _setup(tmp_path)
    response = await _call(dispatcher, "voice.project_vocabulary", {})
    assert "result" in response
    assert response["result"] == {"terms": []}


async def test_project_vocabulary_accepts_optional_project_id(tmp_path: Path) -> None:
    dispatcher = _setup(tmp_path)
    response = await _call(
        dispatcher,
        "voice.project_vocabulary",
        {"projectId": "proj_alpha"},
    )
    assert response["result"] == {"terms": []}


async def test_project_vocabulary_ignores_non_string_project_id(tmp_path: Path) -> None:
    dispatcher = _setup(tmp_path)
    response = await _call(
        dispatcher,
        "voice.project_vocabulary",
        {"projectId": 42},
    )
    assert response["result"] == {"terms": []}
