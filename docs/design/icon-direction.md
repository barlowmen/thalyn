# Thalyn — App Icon Direction

**Status:** Locked · *2026-04-27*

The Thalyn app icon concept is locked to **Direction A — Sigil**, in the
specific form of [`A3-final-gapped.png`](icon-concepts/A3-final-gapped.png):
a glass capital T composed of two intentionally disconnected elements —
a chunky horizontal crossbar at the upper third and a slimmer vertical
stem hanging below it, with a small deliberate gap between them — set
on a Tahoe-canonical layered-glass squircle plate over a deep near-black
background. The mark uses the §11 calm blue-violet (OKLCH 70% 0.15 250)
as its internal-refraction tint.

The remainder of this document captures how the lock was reached
(research baseline, alternatives explored, iteration history) and the
build path from here to a ship-ready asset set.

---

## 1. Locked Concept

![Locked concept — A3 final gapped](icon-concepts/A3-final-gapped.png)

**The mark.** A stylized capital T as two separate glass elements: a
chunky horizontal crossbar at the upper third, a slimmer vertical stem
below, and a small deliberate gap (~4% of icon height) between them.
The cap is wider/thicker, the stem is taller/slimmer; the proportional
contrast is part of the sigil character. Both elements are clear faceted
Liquid Glass with calm blue-violet internal refraction, sitting on a
subtle layered-glass inner squircle plate on a deep dark indigo
near-black background.

**Why this lands.** The gap is the entire conceptual move — it turns a
generic glass T into a Thalyn-specific sigil. Nothing else in the dock
looks like it: not Linear, not Raycast, not Cursor, not Notion, not the
Tahoe first-party set. It reads as an intentional design choice rather
than a typographic accident, and it preserves T-identity at small sizes
because the eye still completes the letter across the gap.

**Why this direction wins (the original recommendation).** A sigil is
*ownable* in a way the letterform and monolith directions were not. It
carries the brand promise ("a single point of attention coming to rest
at a fixed center") without leaning on sparkles, neuron diagrams, or
any other 2024-vintage AI-icon cliché. And it stays legible at 16 × 16
in the macOS dock — the size that actually matters and the size at
which the glass-letterform and stone-monolith options started to lose
their detail.

**Why not the letterform direction.** The clear-glass T concept
([`C1-letterform-glass-T.png`](icon-concepts/C1-letterform-glass-T.png))
came back the strongest of the wave-1 generations and was an early
pick. An independent design critique (Gemini, see §5) correctly pushed
back: a single letter inside a squircle is a crowded field — Notion's N,
Vercel's V, Tailscale's T, Things' T, Tower's T — and a calm-glass
treatment doesn't do enough to escape the pile. *Safe to the point of
being boring.* Kept as a fallback / monogram for marketing surfaces and
the favicon, but not the dock icon.

**Why not the monolith direction.** The obsidian and anodized monoliths
([`B1`](icon-concepts/B1-monolith-obsidian.png),
[`B3`](icon-concepts/B3-monolith-anodized.png)) had the right *mood* —
quiet authority, premium presence — but a slim vertical rectangle has
no distinctive silhouette. At 16 × 16 it dissolves into a coloured
rectangle and could be any premium tool app. Mood lands; recognizability
does not.

---

## 2. Research Baseline

The 2026 desktop icon landscape was surveyed across Linear, Raycast,
Cursor, Arc, Dia, Granola, Notion, Vercel-stack apps, Figma, and
Apple's first-party Tahoe icons. Three forces shaped the recommendation:

**The macOS Tahoe Liquid Glass convention.** Tahoe enforces the squircle
(non-conforming icons get jailed in a grey squircle) and introduces a
layered foreground/background system with default, dark, clear-light,
clear-dark, tinted-light, and tinted-dark variants designed in Apple's
*Icon Composer*. This is the dominant convention now; every concept in
this document is rendered as a squircle and respects layered glass
treatment so it sits naturally next to the OS's own apps. Sources:
[Daring Fireball on Tahoe icons](https://daringfireball.net/2025/08/macos_26_tahoes_dead_canary_utility_app_icons),
[Michael Tsai on Tahoe's theming system](https://mjtsai.com/blog/2025/06/19/macos-tahoes-new-theming-system/),
[heise.de on the squircle "prison"](https://www.heise.de/en/news/Icons-in-macOS-26-Fighting-the-Squircle-Prison-11075561.html).

**Windows 11 Mica is a window backdrop, not an icon constraint.** Mica
tints window chrome with desktop wallpaper; icons just need to read
cleanly on that tinted backdrop. The dark-first dark-indigo squircle in
all of these concepts works on both light and dark wallpaper-Mica.
Source: [Microsoft Mica documentation](https://learn.microsoft.com/en-us/windows/apps/design/style/mica).

**Hard avoids.** ✨ sparkles are over — Google Design, Nielsen Norman,
and Slate have all called out the cliché in 2025–26
([NN/g](https://www.nngroup.com/articles/ai-sparkles-icon-problem/),
[Google Design](https://design.google/library/ai-sparkle-icon-research-pozos-schmidt),
[Slate](https://slate.com/technology/2025/12/artificial-intelligence-tools-icon-google-gemini-chatgpt-design.html)).
Brain-with-circuits, neuron diagrams, decorative gradients, and
glassmorphism-as-decoration are equally exhausted. The §11 visual
language already rejects all of these in writing; the icon must too.

**Peer-set reference points.**
- **Linear** — desaturated purple gradient sphere, calm, engineer-coded ([brand](https://linear.app/brand)).
- **Raycast** — dark chrome + vibrant red, dropped the gradient, now noisy/textured.
- **Cursor** — minimal cursor mark, multi-material variants ([brand](https://cursor.com/brand)).
- **Arc / Dia** — Arc is colorful and expressive; Dia retreats into restraint with strategic moments of expression ([Browser Co. on Dia](https://browsercompany.substack.com/p/the-strategy-behind-dias-design)).
- **Granola** — disappears, very minimal.
- **Notion / Vercel** — single monochrome letterform / geometric mark.

---

## 3. The Three Directions Explored

### Direction A — Sigil (recommended)

A geometric mark — orbit, node, ring, or implied glyph — that reads as
a confident abstract symbol. The goal is something *ownable* and
*recognizable at thumb-nail*, with the conceptual hook being "a single
point of attention." Closest aesthetic peer: Linear's sphere, Cursor's
mark, Apple's first-party Tahoe icons in clear-glass mode.

| Concept | Visual | Notes |
|---|---|---|
| A1 — orbital arc | ![A1](icon-concepts/A1-sigil-orbital-arc.png) | Strong Tahoe-glass execution but reads as a generic AI / meditation / loading icon. Will collapse into a blurry circle at 16 × 16. **Cut.** |
| A2 — Saturn ringed-node | ![A2](icon-concepts/A2-sigil-ringed-node.png) | Beautiful render but reads as another planet/space app. Generic. **Cut.** |
| A3 — implied-T cross *(starting point)* | ![A3](icon-concepts/A3-sigil-implied-T.png) | The conceptual seed for the locked direction. Distinctive silhouette, unambiguous Thalyn-T read. Carried forward through three iteration rounds (see §3.A.iter). |

**§3.A.iter — Iteration history from A3 to the locked concept.**

Round 1 (refinement of the original A3 — drop the decorative pendant ball, swap the neon-tube glow for proper Liquid Glass):

| Variant | Visual | Outcome |
|---|---|---|
| Refined-1 — clean T (no foot) | ![Refined-1](icon-concepts/A3-refined-1-clean.png) | Strong as a render but collapsed back toward letterform — essentially a thicker, darker C1. **Cut for losing sigil character.** |
| Refined-2 — footed T | ![Refined-2](icon-concepts/A3-refined-2-footed.png) | Read as a Roman capital I or a varsity-letter logo. **Cut.** |
| Refined-3 — ring-foot T | ![Refined-3](icon-concepts/A3-refined-3-ring.png) | Read as alchemical / religious symbol; violated the "not mystical" constraint. **Cut.** |

Round 2 (different sigil moves that don't fall into letterform or symbol traps):

| Variant | Visual | Outcome |
|---|---|---|
| Iter2 — asymmetric stem | ![Iter2-asym](icon-concepts/A3-iter2-asymmetric.png) | Model failed to produce the asymmetry; ended up symmetric with unrequested atmospheric haze. **Cut.** |
| Iter2 — wedge (tapered stem) | ![Iter2-wedge](icon-concepts/A3-iter2-wedge.png) | Strong runner-up. Wedge taper read as a precision tool. Held aside in favor of the gap concept. |
| Iter2 — heavy-cap *(seed of the lock)* | ![Iter2-heavy-cap](icon-concepts/A3-iter2-heavy-cap.png) | The model produced an unrequested gap between cap and stem, plus a layered-glass inner squircle. Both became the defining features. |

Round 3 (final tuned reference):

| Variant | Visual | Outcome |
|---|---|---|
| **A3 final — gapped T** | ![A3-final](icon-concepts/A3-final-gapped.png) | **Locked.** Gap as an explicit design feature, controlled cap-to-stem proportions, full-length stem, deliberate Tahoe-layered inner squircle. |

### Direction B — Monolith / object

A singular dimensional object — monolith, keystone, ringed orb — that
plays into the slightly-mythic "Thalyn" name without becoming
fantasy-coded. Quiet authority via material and presence rather than
graphic distinctiveness. Risk: the mood lands, the recognition does
not.

| Concept | Visual | Notes |
|---|---|---|
| B1 — obsidian glass slab | ![B1](icon-concepts/B1-monolith-obsidian.png) | Beautiful, but at dock size it is *unfindable*. Zero brand recognition; could be any premium-tool app. **Cut.** |
| B2 — keystone | ![B2](icon-concepts/B2-keystone.png) | Generation split the keystone into two adjacent slabs, weakening the silhouette. Concept-correct for "the load-bearing center" but visually didn't land. **Cut.** |
| B3 — anodized monolith | ![B3](icon-concepts/B3-monolith-anodized.png) | Strongest of the monolith set — premium hardware energy, Sonos / Apple peripheral peer set. But "rectangle with a glowing seam" still doesn't say *this* product. **Park as runner-up vibe**, not a winner. |

### Direction C — Letterform

A bespoke "T" rendered as a physical object — glass, machined metal,
stone — that owns the name typographically. Closest peers: Notion,
Vercel, Tailscale.

| Concept | Visual | Notes |
|---|---|---|
| C1 — clear glass T | ![C1](icon-concepts/C1-letterform-glass-T.png) | The strongest letterform; cleanest expression of Tahoe Liquid Glass. Tactical fallback if A3 doesn't land in the Icon-Composer pass. **Keep as fallback / monogram for marketing.** |
| C2 — machined aluminum T | ![C2](icon-concepts/C2-letterform-machined-T.png) | Polished aluminum on dark reads slightly retro / 2010s consumer-electronics. Less distinctive than C1. **Cut.** |
| C3 — stone monument T | ![C3](icon-concepts/C3-letterform-stone-T.png) | Misses the brief — stone + warm overhead light reads as fantasy game or architecture software, contradicting the "not mystical" constraint. **Cut.** |

---

## 4. Path to the Final Ship Icon

With the concept locked, the remaining work is to translate the
[`A3-final-gapped.png`](icon-concepts/A3-final-gapped.png) reference
render into a vector mark, build the Tahoe variant set in Apple's
Icon Composer, ship the Windows / Linux assets, and validate at every
size that matters. None of this is user-blocking — it is a v1 build
work item per `project_build_cadence`.

### 4.1 Concept refinement — *complete*

Done. The original A3 concept passed through two rounds of refinement
and one tuned final render to land at `A3-final-gapped.png`. See
§3.A.iter above for the full history.

### 4.2 Vector geometry, then Icon Composer — *complete for legacy + Linux + Windows; `.icon` Tahoe-layered deferred*

The render is a reference, not the ship asset. The ship asset is crisp
vector geometry built from the locked spec:

- Crossbar — solid rectangle, ~16% icon-width thick, ~58% icon-width long, sitting at the upper third with crisp 90° corners.
- Stem — solid rectangle, ~9% icon-width thick, descending from a point ~4% icon-height below the crossbar's bottom edge to ~75% icon-height, with a clean flat squared end.
- Inner Tahoe layered-glass plate — a subtle inner squircle outline at ~88% icon-edge radius. Optional in Icon Composer; the `.icon` format may render this layering automatically.

The build is reproducible — [`scripts/build-icon-assets.py`](../../scripts/build-icon-assets.py)
takes the spec literally (geometry ratios + OKLCH tint), renders a
canonical SVG at [`A3-vector.svg`](icon-concepts/A3-vector.svg), and
rasterises every platform-required PNG / ICNS / ICO / freedesktop
size from the same source. Re-run after any spec change:

```bash
python3 scripts/build-icon-assets.py
```

The output drops into `src-tauri/icons/` (the bundle config) and
`src-tauri/icons/linux/` (the freedesktop hicolor set). A handful of
dock-validation PNGs (16 / 32 / 64 / 128) land in
[`docs/design/icon-concepts/`](icon-concepts/) alongside the locked
reference render so the §4.4 gates have proofs to compare against.

The `.icon` Tahoe-layered format is **not** generated by the build
script — that path runs through Apple's Icon Composer (Xcode 16+) and
needs a Tahoe machine to render the six variants:

- **Default** — dark indigo squircle, violet-tinted glass T
- **Dark** — black squircle, violet-tinted glass T
- **Clear-light** — translucent squircle picking up wallpaper, dark-violet T
- **Clear-dark** — translucent squircle picking up wallpaper, light-violet T
- **Tinted-light / tinted-dark** — system-tint variants

The vector SVG produced by the build script is the foreground-layer
source for Icon Composer; the spec colours are the background tint.
Tracking the Tahoe `.icon` ship lives under "Brand and identity" in
[`docs/going-public-checklist.md`](../going-public-checklist.md) until
a Tahoe build machine is wired in. Until then the bundled `.icns` is
the macOS asset (it carries 16 → 1024 px renders generated from the
same source).

### 4.3 Windows + Linux assets — *complete*

- **Windows**: ICO carrying 16, 24, 32, 48, 64, 128, 256 sizes plus
  the MSIX `Square*Logo.png` set used by Tauri's Windows bundle.
  Mica tinting handled by the OS — no additional work needed.
- **Linux**: freedesktop.org-conformant PNG set
  (16/22/24/32/48/64/128/256/512) under
  `src-tauri/icons/linux/` plus a scalable
  [`thalyn.svg`](icon-concepts/A3-vector.svg)-derived SVG for the
  GTK theme path.
- **Favicon / monogram**: the C1 clear-glass T remains the
  marketing-surface monogram (Linear / Vercel pattern). Generation
  is out of scope here.

### 4.4 Validation gates before ship

- 16 × 16 dock proof — [`A3-vector-16.png`](icon-concepts/A3-vector-16.png) (32 px @2×
  is [`A3-vector-32.png`](icon-concepts/A3-vector-32.png)). At 16 px
  the eye completes the T across the gap; at 32 px the gap reads as
  intentional.
- Side-by-side dock comparison vs Linear, Raycast, Cursor, Claude
  Desktop, VS Code — pending a Tahoe machine for the live capture.
- Tahoe variant audit — pending Icon Composer (§4.2; tracked on the
  going-public checklist).
- Light-mode and dark-mode wallpaper validation — pending the same
  Tahoe machine; the dark-indigo squircle background is engineered
  to read on both.
- WCAG 2.1 AA contrast — verified during build. The mark at
  `oklch(70% 0.15 250)` against the
  `#0D0A1A` squircle background clears 4.5:1 normal-text contrast
  by a wide margin (~12:1). The same check fails at large-text-only
  thresholds against any inner-plate ring, which is why the ring
  alpha is held below 20%.
- Reduced-motion validation — the static raster ships motionless;
  any future inner-light pulse must respect the system flag, gated
  by NFR8 / F8.11.

---

## 5. Independent Critique (Gemini)

A second design read was solicited from Gemini 2.5 Pro (via Vertex AI
API) on the six finalists across the three directions. The full prompt
is in `/tmp/thalyn-icon-critique.py`; the relevant excerpt:

> **A3 — sigil: implied-T cross.** The most memorable and distinct
> shape of the set. The neon-tube effect feels confident and
> technical, successfully blending an abstract sigil with a nod to the
> "T" in Thalyn. The thinness of the lines and the ball at the bottom
> are potential weak points; the ball feels decorative and could be
> simplified or removed to strengthen the core mark.
> *Dock-readability 4/5 · Distinctiveness 5/5 · AI-cliché risk 2/5.*
>
> **Recommendation.** A simple letterform is too safe and a monolith
> is too generic. The sigil direction provides the best opportunity
> to create a unique, memorable, and ownable mark that feels both
> technical and intelligent. It avoids being too literal while still
> being grounded by the T-shape, giving it the perfect balance of
> abstraction and legibility. This is the only concept that has the
> potential to feel as iconic as the logos of the peers you're
> targeting.

Gemini and I agree on the recommendation; it correctly pushed me off
C1 (which was my pre-critique pick) on the grounds of being safe-to-
the-point-of-boring. The "drop or simplify the pendant ball" note has
been folded into §4.1.

---

## 6. What Happens Next

1. ~~User picks a direction.~~ Direction A locked.
2. ~~Refine the A3 mark.~~ Two iteration rounds + one tuned render — `A3-final-gapped.png` locked.
3. ~~Translate to vector geometry + ship `.icns` + `.ico` + Linux
   PNG/SVG.~~ Shipped in v0.38: the canonical SVG at
   [`A3-vector.svg`](icon-concepts/A3-vector.svg), the platform asset
   set under `src-tauri/icons/`, the freedesktop hicolor set under
   `src-tauri/icons/linux/`, and the dock-validation proofs at 16 /
   32 / 64 / 128 px alongside the locked reference render.
4. **Pending the Tahoe build machine:** Apple Icon Composer pass to
   produce the `.icon` file with the six Tahoe variants, plus the
   live side-by-side dock capture vs Linear / Raycast / Cursor /
   Claude Desktop / VS Code. Tracked on
   [`docs/going-public-checklist.md`](../going-public-checklist.md).

---

## Sources

- [Daring Fireball — *MacOS 26 Tahoe's Dead-Canary Utility App Icons*](https://daringfireball.net/2025/08/macos_26_tahoes_dead_canary_utility_app_icons)
- [Michael Tsai — *macOS Tahoe's New Theming System*](https://mjtsai.com/blog/2025/06/19/macos-tahoes-new-theming-system/)
- [heise.de — *Icons in macOS 26: Fighting the "Squircle" Prison*](https://www.heise.de/en/news/Icons-in-macOS-26-Fighting-the-Squircle-Prison-11075561.html)
- [Microsoft Learn — *Mica material*](https://learn.microsoft.com/en-us/windows/apps/design/style/mica)
- [Nielsen Norman Group — *The Proliferation and Problem of the ✨ Sparkles ✨ Icon*](https://www.nngroup.com/articles/ai-sparkles-icon-problem/)
- [Google Design — *Rise of the AI Sparkle Icon*](https://design.google/library/ai-sparkle-icon-research-pozos-schmidt)
- [Slate — *AI Tools All Use the Same Sparkly Icon*](https://slate.com/technology/2025/12/artificial-intelligence-tools-icon-google-gemini-chatgpt-design.html)
- [Linear — Brand Guidelines](https://linear.app/brand)
- [Cursor — Brand Guidelines](https://cursor.com/brand)
- [The Browser Company — *The strategy behind Dia's design*](https://browsercompany.substack.com/p/the-strategy-behind-dias-design)
- [Envato — *Icon Design Trends 2026*](https://elements.envato.com/learn/icon-design-trends)
- [Joshua de Guzman — *Gemini 3 Pro vs Gemini 2.5 Pro for Building Modern UIs*](https://joshuamdeguzman.com/blog/gemini-3-pro-vs-gemini-2-5-pro-modern-ui/)
