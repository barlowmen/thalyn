# ADR-0002 — Frontend stack: React 18 + shadcn/ui + Tailwind + Vite

- **Status:** Accepted (provisional)
- **Date:** 2026-04-25

## Context

We need a frontend stack that pairs well with Tauri 2 (ADR-0001), supports the three-panel mosaic UX (`01-requirements.md` F11.1), gives us a high-quality component library without locking us into a vendor's design tokens, and survives a 3–5 year horizon. Contributor familiarity and AI-assistance friendliness matter — most agentic tooling is most fluent in TypeScript + React.

## Decision

Use **React 18** as the frontend framework, **shadcn/ui** as the component layer, **Tailwind CSS** for styling, and **Vite** as the build tool. shadcn's CSS-variable approach is the carrier for our OKLCH design tokens (ADR-0013). The stack lives entirely inside Tauri's WebView.

## Consequences

- **Positive.** Largest ecosystem of components, tooling, and AI-assist patterns of any frontend choice. shadcn is "copy components into your repo" rather than a versioned dep — we own them and can edit freely. Vite is fast and Tauri-native. The combo is the most-shipped Tauri 2 stack in 2026.
- **Negative.** React 19 adoption is happening in 2026; we'll likely upgrade before v1.0. Tailwind v4 lands during the project lifetime and we should adopt it at the v0.6 review.
- **Neutral.** No CSS-in-JS dependency; Tailwind + CSS variables is the entire styling pipeline.

## Alternatives considered

- **Svelte / SvelteKit + Tauri** — leaner runtime, smaller bundles; rejected for weaker AI-tool fluency and smaller component ecosystem.
- **Solid.js** — appealing performance characteristics; rejected on ecosystem maturity for an IDE-scale UI.
- **shadcn alternatives** (Park UI, Radix-only, Mantine) — rejected; shadcn's "your repo, your code" model best fits our token-driven theming.

## Notes

Re-evaluate React version, Tailwind version, and shadcn equivalents at every architecture review.

### Refinement at v0.2/v0.3 implementation

We adopted **React 19** and **Tailwind v4** when the design system landed in v0.2, ahead of the "v0.6 review" note in **Consequences** above. Both have been stable through v0.3 (provider settings, command palette, chat surface, dialog/input/label/badge primitives) and the migration has paid for itself: Tailwind v4's first-class `@theme inline` block is a much cleaner home for the OKLCH bridge than v3's `tailwind.config.js`. The architecture-review on 2026-04-26 retired the upgrade item.
