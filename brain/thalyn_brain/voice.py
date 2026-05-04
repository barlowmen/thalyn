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

This module owns the vocabulary builder. Two sources feed it:

1. **THALYN.md identifiers.** Code spans (``\\`Foo\\```) are the most
   explicit signal — the user has already marked them as terminology.
   Headings come next: project-named surfaces tend to live there.
   CamelCase / kebab-case / snake_case tokens scraped from prose
   round it out.
2. **Project-scoped memory facts.** ``MemoryStore`` rows tagged to
   the project (and the cross-project user tier) carry the rest of
   the team's vocabulary — names, recurring decisions, jargon the
   user has typed enough times to want recorded.

Whisper's ``initial_prompt`` is small (~224 tokens), so the builder
caps at ``MAX_TERMS`` and orders highest-signal-first so the Rust
core can token-budget-slice without losing the explicit hints.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from thalyn_brain.memory import MemoryStore
from thalyn_brain.project_context import load_project_context
from thalyn_brain.projects import ProjectsStore

MAX_TERMS = 64
"""Cap on the produced term list. Whisper's ``initial_prompt`` only
holds ~150 words after tokenisation; 64 leaves room for the
``"Project glossary: …"`` framing the Rust core wraps around it."""

MAX_MEMORY_ENTRIES = 40
"""Memory rows scanned per session. Recent rows tend to carry the
freshest terminology; older rows fall off the end naturally."""

MAX_THALYN_MD_BYTES = 16_000
"""Cap for the THALYN.md body fed into the identifier scraper. Mirrors
the project-context loader's posture: too-large docs get truncated
rather than fail the whole session."""

# Backtick-quoted code spans, including escaped triple-backticks. The
# inner pattern is non-greedy so adjacent spans don't merge.
_CODE_SPAN_RE = re.compile(r"`+([^`\n]+?)`+")

# ATX-style headings (``# foo`` … ``###### foo``); we strip the leading
# hashes and trailing whitespace + closing hashes per CommonMark.
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(?P<title>.+?)(?:\s+#+\s*)?$", re.MULTILINE)

# Code-shaped identifiers in prose: at least one CamelCase boundary, a
# snake_case underscore, or a kebab-case hyphen between word chars.
_PROSE_IDENT_RE = re.compile(
    r"\b(?:"
    # CamelCase / PascalCase: starts with an upper-case letter and
    # contains at least one further upper-case letter so plain words
    # like ``Thalyn`` still match but plain prose like ``The`` does
    # not. (Single-cap project names land via the heading path.)
    r"[A-Z][a-z0-9]*[A-Z][A-Za-z0-9]*"
    # snake_case
    r"|[A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+"
    # kebab-case (at least one hyphen between word chars)
    r"|[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)+"
    # ALL_CAPS constants (≥2 chars)
    r"|[A-Z]{2,}[A-Z0-9_]*"
    r")\b"
)

# Tokens we drop because they pollute the vocabulary without helping
# decode accuracy. Boilerplate words from common THALYN.md sections
# end up here.
_STOPWORDS = frozenset(
    {
        "TODO",
        "FIXME",
        "NOTE",
        "WARNING",
        "DEPRECATED",
        "XXX",
        "HACK",
    }
)


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


async def build_project_vocabulary(
    *,
    project_id: str | None,
    projects: ProjectsStore,
    memory: MemoryStore | None = None,
) -> ProjectVocabulary:
    """Build the vocabulary slice for a session.

    Pulls identifiers from the project's ``THALYN.md`` (or
    ``CLAUDE.md``) if a workspace path is set, then folds in
    project-scoped memory rows. Returns up to :data:`MAX_TERMS`
    entries, ordered highest-signal-first; duplicates are
    case-insensitively dropped while preserving the first casing
    seen.

    Async because the underlying stores serialise SQLite I/O on a
    thread pool — the dispatcher fans this onto the same event loop
    that handles every other RPC method.
    """
    terms: list[str] = []
    seen: set[str] = set()

    project = await projects.get(project_id) if project_id else None

    if project is not None and project.workspace_path:
        body = _read_thalyn_md(Path(project.workspace_path))
        if body:
            for term in _extract_thalyn_identifiers(body):
                _push_term(term, terms, seen)

    if memory is not None:
        # Project-scoped facts come first (the user wrote them about
        # *this* project), then the personal tier (cross-project
        # vocabulary the user shares across everything).
        rows = await memory.list_entries(
            project_id=project.project_id if project is not None else None,
            scopes=("project", "personal"),
            limit=MAX_MEMORY_ENTRIES,
        )
        for entry in rows:
            for term in _extract_memory_terms(entry.body):
                _push_term(term, terms, seen)

    return ProjectVocabulary(terms=terms[:MAX_TERMS])


def _push_term(term: str, terms: list[str], seen: set[str]) -> None:
    """Append ``term`` if it survives the dedup + stopword filter."""
    cleaned = term.strip(" \t\r\n.,;:!?\"'`()[]{}<>")
    if not cleaned or cleaned in _STOPWORDS:
        return
    key = cleaned.casefold()
    if key in seen:
        return
    seen.add(key)
    terms.append(cleaned)


def _read_thalyn_md(workspace_root: Path) -> str | None:
    """Load the workspace's project-context body, capped to keep
    pathological docs from blowing the regex budget."""
    context = load_project_context(workspace_root, max_chars=MAX_THALYN_MD_BYTES)
    return context.body if context is not None else None


def _extract_thalyn_identifiers(body: str) -> list[str]:
    """Scrape candidate vocabulary tokens from THALYN.md content.

    Order matters — the Rust core trims from the tail when the
    initial-prompt budget is tight, so the most explicit signals
    come first:

    1. Backtick-quoted code spans (the user marked these as terms).
    2. Heading titles (project-named sections live here).
    3. Code-shaped identifiers in prose (CamelCase, snake, kebab).
    """
    found: list[str] = []
    seen: set[str] = set()

    for match in _CODE_SPAN_RE.finditer(body):
        for token in _split_code_span(match.group(1)):
            if token and token.casefold() not in seen:
                seen.add(token.casefold())
                found.append(token)

    for match in _HEADING_RE.finditer(body):
        title = match.group("title").strip(" \t#")
        # Headings often pack multiple terms; split into words and
        # keep only the identifier-shaped ones so generic prose
        # like "Hard rules" doesn't pollute the slice.
        for token in _split_heading(title):
            if token and token.casefold() not in seen:
                seen.add(token.casefold())
                found.append(token)

    for match in _PROSE_IDENT_RE.finditer(body):
        token = match.group(0)
        if token and token.casefold() not in seen:
            seen.add(token.casefold())
            found.append(token)

    return found


def _split_code_span(span: str) -> list[str]:
    """A code span like ``foo.bar(baz)`` should yield ``foo.bar`` and
    ``baz`` rather than the whole punctuated blob — Whisper conditions
    on the literal text and trips on parentheses + dots."""
    parts: list[str] = []
    for chunk in re.split(r"[\s(){}\[\],;:]+", span.strip()):
        if not chunk:
            continue
        parts.append(chunk)
    return parts


def _split_heading(title: str) -> list[str]:
    """Headings are mixed prose. Pull the same identifier shapes out
    of them as the prose scraper, plus the full title if it looks
    like a project-named entity (capitalised first letter, no
    sentence punctuation)."""
    parts: list[str] = []
    if title and title[0].isupper() and not any(c in title for c in ".!?"):
        # Add the whole title for short, capitalised headings
        # ("Hard rules" → kept; "Where to look" → kept; "What is
        # this for?" → dropped because of the ``?``).
        if len(title.split()) <= 6:
            parts.append(title)
    for match in _PROSE_IDENT_RE.finditer(title):
        parts.append(match.group(0))
    return parts


def _extract_memory_terms(body: str) -> list[str]:
    """Memory rows are short prose; pull identifier-shaped tokens
    out the same way the THALYN.md scraper does. Whole-row inclusion
    would balloon the prompt with sentence boilerplate."""
    return [match.group(0) for match in _PROSE_IDENT_RE.finditer(body)]
