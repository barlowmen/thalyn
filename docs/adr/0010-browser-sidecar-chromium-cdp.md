# ADR-0010 — Browser: sidecar headed Chromium driven over CDP

- **Status:** Accepted (provisional)
- **Date:** 2026-04-25

## Context

Thalyn embeds a browser an agent can drive (`01-requirements.md` F4.3). Two approaches are common: (a) embed Chromium in-process via CEF, (b) spawn a headed Chromium as a sidecar process and drive it over the Chrome DevTools Protocol. Production tools (Devin, Manus) have settled on (b); CEF's maintenance burden is significant and version drift causes pain.

## Decision

Spawn **headed Chromium as a sidecar process** and drive it via CDP using either Stagehand v3 (TypeScript, called from the Rust core) or Browser-Use (Python, called from the brain sidecar) — both speak CDP, both ship as actively maintained OSS in 2026. Initial choice: Stagehand v3 (driven from the Rust core), with a Python adapter so brain agents can also issue commands.

## Consequences

- **Positive.** No CEF in our dep tree. Chromium versions independently from Thalyn — security patches don't require us to ship a release. CDP is a stable, well-tooled protocol; debugging an agent's browser session is the same as debugging a regular Chrome DevTools session. Cookies and sessions are isolated per Chromium profile (we ship a per-Thalyn profile).
- **Negative.** Embedding the headed Chromium *window* inside the WebView is non-trivial; cross-OS window-reparenting is shaky. Mitigation: ship as a separate window in v0.x, with an optional "embed as panel via screenshot+DOM mirror" mode if the spike succeeds (`02-architecture.md` §12 risk #1).
- **Neutral.** The user's main browser is untouched — Thalyn's Chromium is its own profile.

## Alternatives considered

- **CEF embedded.** Rejected; maintenance burden, large binary footprint, version-drift pain.
- **Use the system browser via CDP.** Rejected: collides with the user's tabs/cookies/profile.
- **WebView2 (Windows) / WKWebView (Mac).** Rejected: minimal CDP exposure; agents need full DevTools access.

## Notes

We document the "browser pane is a separate window in v0.x" decision visibly in user docs so it's not a surprise.
