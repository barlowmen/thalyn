# ADR-0003 — Code editor: Monaco

- **Status:** Accepted (provisional)
- **Date:** 2026-04-25

## Context

Thalyn embeds a code editor (`01-requirements.md` F4.1). The choice has to balance familiarity (every developer has muscle memory for one editor), AI-assist patterns (ghost-text inline-suggest is table stakes), licensing (we redistribute), bundle size (cold-start matters), and longevity.

## Decision

Use **Monaco** — the editor that powers VS Code, Cursor, Windsurf, Codespaces, and others. MIT-licensed; redistribute freely. Mount inside the React frontend (ADR-0002).

## Consequences

- **Positive.** Universal muscle memory: keybindings, multi-cursor, IntelliSense, command palette, peek-definition, search, minimap, etc. all work the way users expect. Ghost-text inline-suggest is a first-class API surface — the same one Cursor and Copilot use. As long as VS Code is alive (it isn't going anywhere), Monaco can't be quietly deprecated. Active development.
- **Negative.** Bundle size ~2 MB gzipped — meaningful for cold start. Mitigation: lazy-load Monaco after the chat shell paints. LSP integration is plugin-driven, not built-in; we'll wire LSPs explicitly per language.
- **Neutral.** No telemetry in Monaco itself (the data collection is in VS Code, not the editor component).

## Alternatives considered

- **CodeMirror 6** — more elegantly modular, smaller core, the modern stack; rejected for weaker AI-assist ecosystem and the larger amount of IDE-feel we'd reimplement (multi-cursor UX, command palette, etc.).
- **Zed's editor / GPUI components** — beautiful but tightly coupled to Zed; not a library you embed.
- **Build our own atop tree-sitter** — rejected; an editor is a multi-year project on its own.

## Notes

Mitigate bundle-size hit at the v0.10 (editor pane) phase. Re-evaluate against CodeMirror 6 at the v0.6 architecture review specifically if the bundle hits cold-start budgets in NFR1.
