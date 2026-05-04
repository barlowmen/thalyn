"""Voice (STT) — brain-side support for the Rust core's bridge.

The Rust core owns the audio path (cpal capture, whisper.cpp decoding,
transcript streaming back to the renderer) per ``02-architecture.md``
§4.1. The brain's role in voice input is narrower:

- expose a ``project_vocabulary`` slice the engine biases against via
  Whisper's ``initial_prompt`` (the EM metaphor cashing out — voice
  that already knows how the team talks);
- (later commits) record the finalised transcript into the eternal
  thread when the user sends, and into memory when the lead reaches
  for it.

This module ships the vocabulary builder. Real terminology extraction
(THALYN.md identifiers + memory facts merged + de-duplicated) lands
when the project-vocabulary hand-off commit wires it through; the
seam returns an empty list so every later commit can swap in a real
implementation without changing the wire shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from thalyn_brain.projects import ProjectsStore


@dataclass(frozen=True)
class ProjectVocabulary:
    """Vocabulary slice handed to the STT engine via ``initial_prompt``.

    ``terms`` is a flat list — no weights, no namespacing — because
    Whisper's ``initial_prompt`` is just text the model conditions on.
    Order is preserved (most-relevant first) so token-budgeted slicing
    in the Rust core keeps the high-signal entries.
    """

    terms: list[str] = field(default_factory=list)

    def to_wire(self) -> dict[str, list[str]]:
        return {"terms": list(self.terms)}


def build_project_vocabulary(
    *,
    project_id: str | None,
    projects: ProjectsStore,
) -> ProjectVocabulary:
    """Build the vocabulary slice for a session.

    The seam returns an empty vocabulary regardless of inputs — the
    real merge (THALYN.md identifiers + memory facts) lands with the
    project-vocabulary hand-off commit. The signature already accepts
    the inputs that commit will use so the wiring at the call site
    doesn't shift.
    """
    # Touch the inputs so static-analysis recognises them as used —
    # the real implementation will read from both.
    _ = project_id
    _ = projects
    return ProjectVocabulary()
