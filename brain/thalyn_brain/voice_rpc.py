"""JSON-RPC binding for the voice (STT) bridge.

Today this exposes a single method: ``voice.project_vocabulary``,
which the Rust core calls at session start to seed the engine's
``initial_prompt``. Audio bytes never traverse the brain — the core
runs the engine in-process — so there is no ``stt.chunk`` here.

Later commits will add ``voice.transcript_finalised`` (notification
from core to brain when the user sends a voice turn, so memory and
the eternal thread can record it) and ``voice.settings`` (settings
read for the engine routing flag).
"""

from __future__ import annotations

from thalyn_brain.projects import ProjectsStore
from thalyn_brain.rpc import Dispatcher, JsonValue, RpcParams
from thalyn_brain.voice import build_project_vocabulary


def register_voice_methods(
    dispatcher: Dispatcher,
    *,
    projects: ProjectsStore,
) -> None:
    async def project_vocabulary(params: RpcParams) -> JsonValue:
        project_id_value = params.get("projectId")
        project_id = (
            project_id_value if isinstance(project_id_value, str) and project_id_value else None
        )
        vocabulary = build_project_vocabulary(
            project_id=project_id,
            projects=projects,
        )
        return vocabulary.to_wire()

    dispatcher.register("voice.project_vocabulary", project_vocabulary)
