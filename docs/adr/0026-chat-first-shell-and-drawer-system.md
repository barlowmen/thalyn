# ADR-0026 — App shape: chat-first shell + on-demand drawer system

- **Status:** Accepted (provisional)
- **Date:** 2026-04-29
- **Deciders:** Barlow
- **Supersedes:** —
- **Superseded by:** —

## Context

The v1 build shipped a four-panel mosaic shell (activity rail + sidebar
+ surface + chat + inspector). It worked well as scaffolding while the
brain, the leads, the worker layer, the memory store, the eternal
thread, and the connector backbone all came online — every concern got
its own visible region, the user could see the whole machine, and we
could ship surface by surface without designing the relationships
between them.

That same shape is now in the way. F8 in `01-requirements.md` calls for
a **chat-first** experience: the eternal conversation is the primary
surface, every tool opens *on demand* as a drawer, and nothing
permanent lives at the edges of the window. The mosaic ran the wrong
direction — it spent screen real estate to advertise capability,
keeping every tool one click away whether it was relevant or not. The
v2 thesis is the opposite: when the user isn't in a tool, the tool
shouldn't be on screen.

ADR-0013 settled the design tokens (OKLCH + Geist) and chose a
three-panel mosaic as the layout topology. The token system stays
exactly as it was; the layout choice doesn't survive contact with the
v2 requirements.

The drawer-host primitive itself is a separate piece of work
(`` §17, the next phase). This ADR is about the
**topology** — the shape the user sees when the drawer-host primitive
slots in — and about the **migration path** that gets us from the v1
mosaic to the v2 shell without ripping out the working surfaces.

## Decision

The app shell is **chat-first**, with five named regions:

```
┌─────────────────────────────────────────────────────────┐
│  Top bar (~52 px)                                       │
│  ◉ Thalyn · Claude Sonnet 4.6   [Thalyn ▾]   ⌘K   ⚙    │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  Eternal chat (fluid)                                   │
│  · day-dividers, project-tag pills, lead-attribution    │
│    chips, confidence flags                              │
│                                                         │
├─────────────────────────────────────────────────────────┤
│  Transient progress strip (~36 px, only when in flight) │
├─────────────────────────────────────────────────────────┤
│  Composer (~72 px)                                      │
└─────────────────────────────────────────────────────────┘
```

- **Top bar.** Thin (~52 px). Brain identity badge on the left, project
  switcher pill in the middle, Cmd-K hint and settings cog on the
  right. No menu bar — every menu action is reachable through the
  command palette.
- **Eternal chat.** Fluid, fills the window between top bar and
  composer (or transient strip when present). Generous typography,
  centred reading column up to a max-width for long-form scanning.
  Day-dividers between turns that span calendar days; project-tag
  pills surface which project a turn relates to; lead-attribution
  chips flag turns that the brain delegated; confidence flags surface
  the lead → brain critic's uncertainty signal when present.
- **Transient progress strip.** Only present when something is in
  flight (a lead is working, a worker is awaiting approval, a drift
  check has flagged a run). When idle, the strip is gone and the
  chat reaches all the way to the composer. Click opens the
  relevant detail drawer.
- **Composer.** Single input at the bottom. Cmd-Enter sends. Slash
  commands inline. Voice mic button as a stub at this stage; the
  full voice path lands later (F7).
- **No permanent rails, no permanent inspector, no surface tabs.**
  Editor / Terminal / Browser / Email / Connectors / Agents / Logs
  all become drawer-hosted destinations once the drawer-host
  primitive lands.

The drawer-host primitive (right ~40% of the window, dismissible with
⌘\\, two drawers max, chat always ≥ 1/3 of the window) is **not part of
this ADR** — it lands with the next phase's work and gets its own ADR
revision if its shape diverges from F8.2. This ADR fixes the topology
the drawers will slot into.

### Migration path: `/legacy` keeps v1 surfaces reachable

The chat-first shell ships before the drawer system does. Tearing out
the v1 surface tabs entirely would leave a window of phases where the
editor / terminal / browser / email / connectors / agents / logs
surfaces work but aren't reachable.

The migration path is a **`/legacy` route** that renders the v1
four-panel mosaic shell unchanged. The default route (`/`) renders
the chat-first shell; users who want the old behaviour navigate to
`/legacy` (or the brain's "Open the legacy surface" command in the
palette). When the drawer-host primitive lands and the surfaces are
re-homed, the `/legacy` route gets removed and the v1 mosaic shell
deletes with it.

Routing is intentionally **minimal**: a `usePathname` hook reads
`window.location.pathname` and listens for `popstate`, no router
library. The app has exactly two routes during the transition; a
20-line hook is the right tool. When v0.27's drawer system lands
and `/legacy` retires, the hook can retire too, or graduate into
something larger if the drawer state-machine wants URL persistence.

## Consequences

- **Positive.**
  - **The visible surface matches the product.** Chat is what the user
    came here to do; the shell stops competing for attention. F8.1's
    promise — *no permanent left rail, no permanent right inspector,
    no surface tabs* — is satisfied at the topology level the moment
    this lands, even though drawers themselves come next.
  - **Re-homing the surfaces is purely additive.** v1's surfaces
    keep working under `/legacy` until the drawer-host primitive can
    host them as drawers. The cutover is incremental, surface by
    surface, with the safety net of the legacy shell behind it.
  - **The transient strip is a single, unmissable signal.** v1's
    inspector panel showed every active run as a permanent fixture
    even when none were running; the transient strip is gone when
    idle and present (and clickable into detail) when not. Aligns
    with the `01-requirements.md` §11 calm-density positioning.
  - **The `/legacy` route deletes cleanly.** No stranded code: the
    moment every surface has a drawer home, the legacy mosaic shell
    and the route guard delete together.
- **Negative.**
  - **Two app shells in the tree at once.** Until `/legacy` retires,
    the codebase carries both the chat-first shell and the v1
    mosaic shell. The duplication is intentional (it's the safety
    net) but it costs maintenance attention until v0.27 closes.
  - **The drawer-host primitive isn't here yet.** A user who clicks
    "Open editor" in the chat-first shell during this transition
    has no editor to open; they go through `/legacy` to reach it.
    F8.2 closes the gap; this ADR consciously ships before then.
  - **Layout-related localStorage keys split.** The mosaic shell
    persists its panel sizes under `thalyn:layout:default`; the
    chat-first shell doesn't have panel sizes to persist. Users
    bouncing between routes during the transition see independent
    state. Not worth synchronising.
- **Neutral.**
  - **Tokens unchanged.** OKLCH + Geist (ADR-0013) carries forward
    intact. The pivot is layout-only; nothing about colour, type,
    or motion is being redecided here.
  - **Cmd-K still opens the palette.** The palette implementation
    (`src/components/command-palette.tsx`) ports across as-is; the
    chat-first top bar exposes the same shortcut hint that the v1
    rail did.

## Alternatives considered

- **Keep the four-panel mosaic and just hide rails by default.**
  Rejected. F8.1 requires the surfaces to be drawers (transient,
  dismissible), not collapsed panels. A hidden permanent panel is
  still a permanent panel — it advertises the capability in the
  empty-collapsed state and reserves a layout commitment we no
  longer want to make. The mosaic is the wrong frame.
- **Pivot to chat-first and tear the surfaces out in the same phase.**
  Rejected. Doing both at once means a ~3 phase window where editor /
  terminal / browser / email / connectors / agents / logs are
  unreachable while the drawer host gets built. Even with
  feature-flag rollback, the tear-out-first ordering trades regress-
  ability for raw lines-of-code-removed; the legacy route is the
  more conservative path.
- **Use a router library (react-router, tanstack-router) for the
  `/legacy` split.** Rejected. We have exactly two routes during the
  transition and zero after. A 20-line `usePathname` hook is
  smaller, has no version churn, and deletes cleanly when `/legacy`
  retires. Adopting a router for two routes is the kind of premature
  abstraction `.claude/CLAUDE.md` warns about.
- **Keep v1's inspector panel but make it transient.** Rejected.
  The inspector's job — show me everything that's running — is what
  the transient strip does, but the inspector also crowded the chat
  region and rendered a full surface even when one run was active.
  The transient strip carries the same information density without
  the permanent layout commitment.

## References

- `01-requirements.md` §F8 (UI shape: chat-first, drawer-based),
  §11 (visual language).
- `02-architecture.md` §4.3 (React frontend component view).
- ADR-0013 (design tokens; the mosaic-layout claim is what this
  ADR refines, not the token system).
- `docs/design/icon-direction.md` (locked icon direction; informs
  the brain identity badge styling).

## Notes

This ADR ships at the start of the chat-first pivot with `Status:
Accepted (provisional)`. It flips to `Accepted` when the drawer-host
primitive lands and the `/legacy` route retires — at that point both
of this ADR's load-bearing claims (chat-first topology, drawers as
the migration target) have been exercised end-to-end with no escape
hatch. Until then, the provisional marker keeps the door open for
the topology to re-shape if the drawer experiments uncover something
this ADR didn't anticipate.
