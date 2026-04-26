# ADR-0013 — Design system: OKLCH tokens, Geist typography, three-panel mosaic

- **Status:** Accepted (provisional)
- **Date:** 2026-04-25

## Context

Thalyn must feel modern, beautiful, and intuitive (`01-requirements.md` F11 / §11). A coherent token-driven design system is the prerequisite — without it, every component reinvents spacing, color, and motion. The system must support light + dark + system-following themes, accessibility (WCAG 2.1 AA contrast across both modes), reduced-motion, and reduced-transparency, and must be easy for an agent to extend without breaking visual consistency.

## Decision

A token-driven system with:

- **Color** in **OKLCH** for perceptually uniform lightness across hues. Dark-first; light is a tonal inversion. Single primary accent (`oklch(70% 0.15 250)` — calm blue-violet, revisable).
- **Typography**: **Geist Sans** (variable) for UI, **Geist Mono** (variable) for code/terminals. No third family.
- **Layout**: **three-panel mosaic** (activity rail + sidebar + main + inspector), all panels resizable and collapsible.
- **Motion**: **Motion** library (renamed Framer Motion). Three durations (150 / 250 / 400 ms), one easing default, bounce only on completion moments.
- **Iconography**: Lucide as the primary set; custom icons follow Lucide's 24 px / 1.5 px stroke language.
- **Surfaces**: glass/vibrancy only on the topmost layer (chat input bar, command palette, modals); flat-with-elevation everywhere else. No skeumorphism, no neumorphism.

Tokens live in `src/design/tokens.css` (CSS custom properties) with a TypeScript mirror at `src/design/tokens.ts`. shadcn/ui (ADR-0002) consumes the CSS variables.

## Consequences

- **Positive.** Coherent visual language out of the box. OKLCH guarantees consistent contrast across both themes and across hues — accessibility is a property of the token system, not per-component vigilance. Geist + Lucide are free and well-maintained. Storybook + axe-core in CI catches regressions.
- **Negative.** OKLCH is unfamiliar to some designers; we'll need a brief authoring doc.
- **Neutral.** Accent color is one token away from being changed; not a permanent commitment.

## Alternatives considered

- **Tailwind defaults (zinc / slate, hex-based colors).** Considered; rejected for less consistent perceptual lightness.
- **Material You / Material 3.** Rejected; doesn't match the calm-density positioning.
- **Custom typeface.** Rejected; Geist is excellent and free.

## Notes

Visual-design decisions are user-changeable; the token files are the single source of truth and are revisable per release.
