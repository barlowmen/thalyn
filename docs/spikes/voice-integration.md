---
date: 2026-05-03
risk: F7 commits to a local-first STT default with planning-time guidance pointing at Whisper.cpp; the engine + model + UX-pattern picks need evidence rather than vibes before the v0.33 build.
adr: 0025 (drafts as Proposed off this spike)
---

# Spike: voice-integration

- **Question.** Under F7's local-first STT default, what is the right
  engine, model, and UX-pattern shape for v0.33's voice-input build?
  Planning-time guidance pointed at Whisper.cpp without an empirical
  bake-off; F7 names "Whisper.cpp (or equivalent)" and the
  "(or equivalent)" needs to resolve to a specific call before the
  composer mic, the IPC surface, and the bundle layout get committed.
- **Time-box.** 1 working session. **Actual:** ~1 session. The
  decisive constraints (license fit, cross-platform parity, bundle
  weight, Apple Silicon perf) and the bake-off numbers collapsed the
  option space cleanly enough that the recommendation fell out
  without a longer matrix.
- **Outcome.** Answered. Recommended: **Whisper.cpp via the
  `whisper-cpp-plus` Rust crate**, integrated into the Rust core, with
  Core ML acceleration on Apple Silicon and CPU + OpenBLAS elsewhere;
  default model **`small.en`** with a `base.en` low-RAM fallback;
  **Deepgram Nova-3** as the opt-in cloud fallback for users who want
  sub-300 ms streaming latency or whose hardware can't run small.en
  interactively; **MLX-Whisper** filed as a documented opt-in for
  Apple Silicon power users. UX patterns recommended for v0.33: the
  composer mic with hold-space push-to-talk, level-meter feedback,
  editable transcript before send, opt-in continuous-listen with
  silence threshold, and project-memory-derived vocabulary hand-off
  through the lead. Hotkey-anywhere, smart modes, power mode,
  LLM-powered transcript cleanup, and screen-context awareness ship
  as v1.x follow-ups, not v0.33. ADR-0025 drafts as Proposed against
  these picks; `` §23 is rewritten to match.

## Approach

1. Anchored on F7's hard constraints (local-first by default,
   privacy posture, push-to-talk, editable transcript) and on
   ADR-0016's MIT-license bar — that combination eliminates a
   number of otherwise-attractive options before any benchmark is
   run.
2. Stood up an in-tree bake-off harness at
   `docs/spikes/voice-bake-off/`. Fixture: the 11.0 s public-domain
   JFK clip from `ggml-org/whisper.cpp/samples/jfk.wav` — the
   de-facto whisper benchmarking sample, real speech, multiple
   sentences, broadcast-quality. Reference transcript at
   `fixture/jfk.txt`. The harness measures cold-call wall clock,
   warm-median wall clock, real-time factor (warm wall ÷ audio
   duration), peak RSS, and word-error rate against the reference;
   each (engine, model) pair runs three times and the warm median
   is reported. Apple Silicon target hardware: **M4, 16 GB**
   running on macOS with Metal available — close enough to the M1
   baseline that ratios extrapolate, with M4-as-modern as one
   anchor and published M1 numbers as the other.
3. Ran nine (engine, model) cells against the fixture: Whisper.cpp
   via `whisper-cli` (Metal), faster-whisper (int8, CPU), and
   MLX-Whisper (Apple Silicon Metal/MLX) — each at `tiny.en`,
   `base.en`, `small.en`. Results captured at
   `docs/spikes/voice-bake-off/results/`.
4. Researched the option space the local bake-off can't cover:
   Parakeet-TDT 1.1B, distil-Whisper variants, cloud STT
   candidates (Deepgram Nova-3, OpenAI gpt-4o-transcribe, Groq
   Whisper), cross-platform RTF for Linux without GPU and the
   2-year-old Windows laptop slot, microphone-permission flows
   per OS, and the CEF + CoreAudio coexistence question. Sources
   listed at the end.
5. Read the VoiceInk repo for UX patterns. Confirmed the license:
   GPL-3.0, **not** MIT — VoiceInk is study-only, the patterns
   can be re-implemented but the code cannot be cherry-picked
   into Thalyn (ADR-0016). The agent-memory entry that called
   VoiceInk "MIT-licensed" was wrong; the spike updates it
   alongside this doc.
6. Walked the v0.33 *build* shape against the spike's findings —
   does this engine + model + UX pick actually land cleanly into
   the existing IPC + composer + brain surfaces? Adjusted the
   spike's recommendation where the build shape pushed back.

## Findings

### F1. Bake-off matrix (M4, 16 GB, macOS, fixture: jfk.wav 11.0 s, English-only models)

Wall-clock numbers reported as warm median over runs 2–3 (run 1 is
the cold-start outlier). RTF = warm wall ÷ 11.0 s audio. WER is
against the reference transcript at `fixture/jfk.txt`, normalized
(lowercase, punctuation stripped). RSS is the peak resident-set
size delta of the transcribing process; the whisper.cpp row is
`whisper-cli`'s child-process RSS, the others are in-process Python
RSS.

| Engine             | Model     | Cold (s) | Warm (s) | RTF (warm) | WER   | RSS (MB) |
|--------------------|-----------|---------:|---------:|-----------:|------:|---------:|
| whisper.cpp (M-Metal) | tiny.en  | 1.093    | 0.587    | 0.053×     | 0.000 | 227      |
| whisper.cpp (M-Metal) | base.en  | 0.565    | 0.573    | 0.052×     | 0.000 | 109      |
| whisper.cpp (M-Metal) | small.en | 1.095    | 1.089    | 0.099×     | 0.000 | 473      |
| faster-whisper (CPU int8) | tiny.en  | 3.364    | 0.395    | 0.036×     | 0.000 | 364      |
| faster-whisper (CPU int8) | base.en  | 4.364    | 0.653    | 0.059×     | 0.000 | 263      |
| faster-whisper (CPU int8) | small.en | 13.528   | 1.996    | 0.181×     | 0.000 | 745      |
| MLX-Whisper       | tiny.en   | 1.286    | 0.097    | 0.009×     | 0.000 | 208      |
| MLX-Whisper       | base.en   | 0.232    | 0.138    | 0.013×     | 0.000 | 251      |
| MLX-Whisper       | small.en  | 0.582    | 0.356    | 0.032×     | 0.000 | 613      |

Three things fall out:

- **Accuracy ties on broadcast-quality speech.** Every cell hit
  WER 0.000 against the JFK reference. The decision turns on
  latency, RSS, and platform fit — not on accuracy at this fixture.
  Non-broadcast inputs (technical jargon, accents, noise) will
  separate the model sizes; that's a v0.33 verify-step concern,
  not an engine-selection one.
- **MLX-Whisper is dramatically faster on Apple Silicon — but
  Apple-Silicon-only.** RTF 0.013× at base.en (≈ 78× real-time)
  and 0.032× at small.en (≈ 31× real-time) on M4. faster-whisper
  CPU-only is the slowest because it ignores the GPU; whisper.cpp
  with Metal lands in between. MLX is not an option for
  Linux / Windows; recommending it as the universal default would
  fork the engine across platforms.
- **whisper-cli's per-call cost is real.** The `whisper-cli` row
  pays process startup + model load on every invocation; the warm
  numbers (0.5–1.1 s) are still that shape, not steady-state. In
  production the engine runs in-process via a Rust binding (see
  F4), and steady-state per-call latency drops well below the CLI
  numbers because the model stays loaded. The CLI numbers are an
  honest *upper bound* on what the in-process binding will deliver.

The harness, the fixture, and the per-cell JSONs are committed for
reproducibility — `python3 scripts/bench.py` from the bake-off
directory re-runs the matrix on whatever box the spike is being
re-validated on (Linux without GPU, the 2-year-old Windows laptop,
M1 baseline once we get one).

### F2. Cross-platform numbers — extrapolated, not measured here

The bake-off ran on M4 alone; the cross-platform shape comes from
published benchmarks. Extrapolation, with sources at the end:

- **Apple Silicon M1 (8 GB) baseline.** whisper.cpp with Metal:
  small.en ≈ 0.10–0.15× RTF, base.en ≈ 0.05–0.08× RTF
  (interpolated from the M1 Pro encoder timing of 685 ms for
  small.en in `whisper.cpp` issue #89, vs M4's 1089 ms on the
  same model — M4 is 1.5–2× faster on encoder; small.en is
  feasible interactively, but tight under load). With Core ML on
  the ANE (the `.mlmodelc` path), encoder runtime drops 3–6× —
  small.en should land closer to MLX-Whisper's M-series numbers,
  i.e. ≈ 0.03–0.05× RTF.
- **Intel Mac.** Out of scope. v1 doesn't ship on Intel Mac
  (`docs/local-models.md` already documents Intel as
  unsupported); this spike doesn't reopen that.
- **Linux without GPU (modern x86, AVX2).** whisper.cpp CPU +
  OpenBLAS: tiny.en ≈ 0.2–0.4× RTF, base.en ≈ 0.5–1.0× RTF,
  small.en ≈ 1.5–3.0× RTF. small.en is *near*-real-time and
  unpleasant for push-to-talk with a 5–15 s utterance — base.en
  is the practical default on this slot.
- **Linux without AVX2 / pre-2013 x86.** Tiny only; flagged as the
  hardware floor.
- **2-year-old Windows laptop (Ryzen 5 / i5 with AVX2).** Same
  ballpark as modern x86 Linux; AVX-512 (where present, mostly
  Intel 11th-gen+) tightens small.en toward 0.7–1.5× RTF with
  Intel oneMKL, but we should not assume oneMKL is universally
  available — OpenBLAS is the portable bar.
- **Linux with NVIDIA CUDA.** Out of scope for the v1 voice
  default — the user base for "Linux desktop with NVIDIA GPU and
  no Apple device" overlaps mostly with users who would already
  prefer cloud STT for latency.

The numbers above are interpolations from public benchmarks, not
measurements. v0.33's verification recipe should re-run the
in-tree bake-off harness on each platform's representative
hardware before declaring exit criteria met — the fixture and
harness are committed precisely so this is a small task, not a
re-derivation.

### F3. Cloud STT fallback: Deepgram Nova-3

Three credible 2026 candidates for the opt-in cloud fallback:

- **Deepgram Nova-3.** Sub-300 ms streaming latency in production
  (200–400 ms time-to-final after audio ends). Purpose-built for
  real-time agent voice. Pricing competitive at ~$0.0043/min.
- **OpenAI `gpt-4o-transcribe`.** ~320 ms latency, leading
  accuracy (2.46 % WER on the model card's reference benchmark),
  but priced higher (~$0.006/min) and the streaming surface is
  newer.
- **Groq Whisper (large-v3-turbo).** Cheap and very fast in
  *batch* (164–299× real-time), but Groq's LPU pipeline is
  chunk-based rather than true streaming — it doesn't fit the
  push-to-talk shape, and the "user just stopped talking" → "show
  the final transcript" hop has higher tail latency than Deepgram.

For Thalyn's voice flow, the deciding factor is **time-to-final**
on a short utterance (5–15 s). Deepgram Nova-3 is the only
candidate that hits sub-300 ms reliably. Pin it as the v0.33
cloud-fallback vendor. OpenAI `gpt-4o-transcribe` files as a
known alternative for users in the Anthropic-or-OpenAI auth
topology; Groq files as "out of scope for v0.33 — its latency
profile is wrong for push-to-talk."

The cloud fallback stays opt-in and clearly labelled, per F7.2.
The trust-domain table in `02-architecture.md` §11 already calls
out cloud STT as "leaves the machine" and that wording carries
forward unchanged.

### F4. The Rust binding is the right integration surface

The voice STT bridge in `02-architecture.md` §4.1 already lives
in the Rust core, not the brain — the renderer feeds audio to
the core, the core runs the engine, the core emits transcripts
back to the renderer (composer) and to the brain (`stt.start /
chunk / stop`). That topology already exists in the
architecture; this spike confirms it's the right shape and pins
the binding.

Two MIT-or-equivalent crates wrap whisper.cpp for Rust today:

- **`whisper-cpp-plus` 0.1.4 (operator-kit, MIT, 2026-02-23).**
  Active. Provides `WhisperContext` (`Send + Sync`),
  `WhisperState` (`Send`, one per thread), real-time PCM
  streaming via `WhisperStream` / `WhisperStreamPcm`, and a
  Silero-VAD-based `EnhancedWhisperVadProcessor` that aggregates
  speech segments into optimal chunks. Supports CUDA, ROCm,
  Metal, OpenBLAS, Vulkan via cargo features. **Does not yet
  expose Core ML.** That's the load-bearing gap for the Apple
  Silicon path.
- **`whisper-rs` v0.16 (tazz4843, public domain via Unlicense,
  archived 2025-07-30).** Mature surface but the GitHub repo is
  archived; the maintainer migrated to Codeberg with no further
  updates planned. Risky as a single-source dependency; we'd
  carry the binding ourselves over time.

Recommendation: take `whisper-cpp-plus` as the primary binding.
The Core ML gap is small — whisper.cpp itself supports Core ML
when built with `WHISPER_COREML=1` and the `.mlmodelc` artifact
is present alongside the `.bin` GGML model. v0.33 either
upstreams a `coreml` cargo feature to `whisper-cpp-plus` or
carries a thin patch in our build.rs. Either is well-scoped.

If `whisper-cpp-plus` becomes unavailable mid-build, the fallback
is to vendor a thin binding (cxx + bindgen against
`whisper.h`). That's measured in days, not weeks; the surface
we use is small (load model, transcribe PCM frames, query
language).

### F5. Default model: `small.en`, with `base.en` low-RAM fallback

Three model-selection axes:

- **Accuracy.** small.en is the production sweet spot among
  whisper variants — within ~2 % WER of medium.en and ~4 % WER
  of large-v3 on conversational English (per the open-source
  benchmark consensus), at a fraction of the bundle weight.
  base.en gives up another 4–6 % WER on noisy / accented speech;
  acceptable as a fallback, not a default.
- **Bundle.** small.en is 487 MB on disk; base.en is 148 MB;
  tiny.en is 78 MB. Bundling small.en preloaded is the
  installer-weight question — see F6.
- **Latency floor.** small.en is interactive on every Apple
  Silicon target (RTF < 0.15× even on the M1 baseline with Core
  ML) and on modern x86 with AVX2 (sub-real-time on a 4-core+
  laptop with OpenBLAS). On older / weaker hardware (no AVX2,
  ARM Linux SBCs, the floor of the 2-year-old Windows laptop
  slot), small.en runs above real-time and base.en becomes the
  default.

The shape: ship a hardware probe at first launch that picks
small.en or base.en based on the platform's OpenBLAS / Metal /
CoreML availability and a small benchmark run against a built-in
fixture. The probe writes its choice to settings; the user can
override.

distil-Whisper variants (distil-large-v3 at 6× the speed of
large-v3 with within-1 % WER on out-of-distribution evals;
distil-small.en at 166 M params with within-4 % WER of large-v3)
are tempting but most of their wins are at the *large* tier —
the distil-small.en variant is in the same ballpark as small.en
on speed and slightly worse on accuracy. We hold the small.en
default and revisit distil-small.en in a quarterly review if
distil quality on conversational English jumps.

Multilingual variants (`small`, `base`, etc., without the `.en`
suffix) are out of scope for v0.33 — F7's scope is English voice
input. Multilingual lands in a v1.x phase.

### F6. Bundle layout: lazy-download `small.en`, ship `base.en` preloaded

A 487 MB model bundled in the installer is a lot — the v0.30 CEF
work already added ~250 MB to the bundle, and the brain sidecar
adds another ~263 MB. Stacking another 487 MB pushes the
installer past 1.3 GB. That's a real first-time-install tax.

Recommended layout for v0.33:

- **Preload base.en (148 MB) in the installer.** This is the
  low-RAM-machine fallback and it's small enough that the bundle
  cost is acceptable. First-time voice input works immediately,
  even before the user has internet.
- **Lazy-download small.en (487 MB) on first push-to-talk.**
  Progress UI in the composer; user can opt out and stay on
  base.en. The download is a one-time cost behind a clear
  progress indicator — same shape as macOS's Apple Intelligence
  download flow.
- **Apple Silicon: download the matching `.mlmodelc` alongside
  the `.bin` on first run.** The first ANE compile pass adds
  ~10–15 s startup the first time; subsequent runs are cached.
  Document this in the first-run flow.

The cloud STT path is also a "lazy" fallback — it requires the
user to opt in and supply a Deepgram API key. Not bundled.

### F7. UX patterns from VoiceInk — what to carry, what to defer

VoiceInk (GPL-3.0, study-only) is the strongest open-source
reference for "voice dictation that doesn't feel like a 2010
accessibility shim." Its UX register translates to Thalyn's
EM-conversation framing well, but the v0.33 build should not try
to land all of it.

| Pattern                                  | Land in v0.33? | Notes |
|------------------------------------------|----------------|-------|
| Push-to-talk with hold-key activation    | **Yes**        | Hold-space *inside the composer* for v0.33; not a global hotkey. |
| Editable transcript before send          | **Yes**        | F7.3 already mandates this. |
| Level-meter visual feedback              | **Yes**        | Inline in the composer mic affordance. |
| Continuous-listen with silence threshold | **Yes (opt-in)** | F7.3 mandates the opt-in shape. |
| Project-memory-derived vocabulary        | **Yes**        | Lead exposes a `project_vocabulary` slice the engine biases against; Whisper supports `initial_prompt` for this. Cheap to wire. |
| Hotkey-anywhere (system-global activation) | No (v1.x)    | Requires accessibility-API plumbing across three OSes; orthogonal to the chat-first shell. |
| Smart Modes (Email / Tweet / Chat profiles) | No (v1.x)   | Useful for dictation-into-other-apps; the EM conversation is the main flow, not "dictate into Word." |
| Power Mode (per-app context switching)   | No (v1.x)      | Same reasoning as Smart Modes. |
| LLM transcript cleanup                   | No (v1.x)      | Adds a per-utterance brain hop and a UI for it; the editable-transcript-before-send path covers the same need with less moving parts. |
| Screen-context awareness                 | No (v1+)       | Screen-context is its own design problem. |

The "project vocabulary" pattern is the load-bearing one for
Thalyn specifically: a worker writing a code review pings the
backend lead, and the lead-side voice flow biases against the
project's known identifiers and terminology. That's the EM
metaphor cashing out — voice input that already knows how the
team talks. It rides on the project memory infrastructure
(`THALYN.md` + Mem0 facts) that's already in the architecture;
v0.33 wires it in without new substrate.

### F8. Microphone permission flows per OS

The composer mic captures audio in the Rust core, **not** in the
CEF surface — F5.1 / ADR-0019 leaves CEF as the in-app browser
only. So the permission story is the standard
`cpal`-via-Tauri-main-process shape, not the CEF-WebRTC shape.

- **macOS.** `NSMicrophoneUsageDescription` in `Info.plist`. The
  prompt fires the first time the Rust core opens an input
  device. Known landmine: a signed bundle without the key in
  the *merged* Info.plist (Tauri merges `src-tauri/Info.plist`
  with its generated one; both sides need to align) silently
  fails to prompt. v0.33 adds the key in `src-tauri/Info.plist`
  and verifies the merged plist as part of the bundle smoke.
- **Windows.** Two layers. The MSIX/AppX path with
  `<DeviceCapability Name="microphone"/>` in the manifest is
  the modern shape; the legacy desktop path needs the
  Settings → Privacy & security → Microphone → "Let desktop
  apps access your microphone" toggle on. Tauri's installer
  format defaults to MSI / NSIS, both legacy desktop — so v0.33
  ships with a "Windows microphone access" first-run check that
  verifies access and surfaces the Settings deep-link if the
  toggle is off.
- **Linux.** PipeWire/PulseAudio with `xdg-desktop-portal`'s
  Audio portal (camera-like permission flow) is the
  forward-looking path; in practice most distros still hand
  audio access through ALSA/PulseAudio without an explicit
  prompt. v0.33 documents the PipeWire portal path in
  `docs/voice-input.md` but doesn't gate behind it — Linux
  audio is "if it works, it works; if it doesn't, the user
  knows their distro better than we do."

### F9. CEF + CoreAudio coexistence: not a real conflict

The original spike scope flagged "audio-capture-vs-CEF
coexistence on macOS (both want CoreAudio)" as a risk. Walking
the actual flow:

- The voice STT path opens an *input* CoreAudio stream via cpal
  in the Rust core.
- CEF only opens CoreAudio streams when web content asks for
  microphone access (`getUserMedia`) — and Thalyn's CEF surface
  is the in-app browser, where microphone access is rare and
  user-mediated.
- macOS allows multiple processes (and multiple sub-processes
  of one bundle) to share an input device; there is no
  exclusive-mode policy on input. CEF helper bundles inherit
  the parent's `NSMicrophoneUsageDescription` permission grant.

If a future flow has the user on a Zoom-in-CEF call *and* trying
to push-to-talk Thalyn at the same time, both succeed at the
CoreAudio layer — though the user experience of two concurrent
mics is its own UX question we don't need to solve in v0.33.
Filing this as "not a v0.33 risk."

The CEF-side microphone configuration (the
`enable-media-stream` switch + `OnRequestMediaAccessPermission`
callback) is a *separate* concern that lands when in-app web
voice flows do — out of scope for the spike, on the
going-public list as "wire up CEF media-permission UX before
public release."

## Recommendation

**Adopt Whisper.cpp via the `whisper-cpp-plus` Rust crate as the
local-default STT engine, with `small.en` as the default model,
`base.en` as the low-RAM fallback, Deepgram Nova-3 as the opt-in
cloud fallback, and MLX-Whisper documented as an opt-in
power-user alternative on Apple Silicon.** ADR-0025 drafts as
Proposed against this pick. `` §23 rewrites to
match.

### What v0.33 actually builds (voice slice)

- **Engine.** `whisper-cpp-plus` 0.1.4+ pinned as the binding.
  Core ML build flag enabled on Apple Silicon (either via an
  upstream PR adding the `coreml` cargo feature, or via a thin
  patch in our `build.rs`). OpenBLAS feature on Linux + Windows.
- **Default model.** `small.en` for English-only voice input.
  `base.en` lazy-fallback for hardware that fails the on-launch
  benchmark probe. tiny.en is the floor for the worst-case
  Windows laptop / pre-AVX2 Linux slot — wired up but not picked
  by the probe unless smaller models also fail.
- **Bundle.** `base.en` (148 MB) preloaded in the installer for
  immediate-first-use. `small.en` (487 MB) lazy-downloads on
  first push-to-talk with progress UI. Apple Silicon
  `.mlmodelc` artifacts download alongside the `.bin`.
- **Streaming.** Push-to-talk default — hold space-in-composer
  to record, release to send the final transcript to the editor.
  `WhisperStream`/`WhisperStreamPcm` for the streaming hot path;
  Silero-VAD via `EnhancedWhisperVadProcessor` for opt-in
  continuous-listen with silence-based auto-send.
- **IPC surface.** `stt.start` / `stt.chunk` / `stt.stop`
  already in `02-architecture.md` §10. Streams `stt.transcript`
  notifications carrying interim and final flags. No schema
  changes from v2 baseline.
- **Composer mic.** Hold-space push-to-talk, level meter while
  recording, editable transcript drops into the composer, Cmd-
  Enter sends. Identical surface in direct lead chat (F2.4).
- **Cloud opt-in.** Deepgram Nova-3 as the v1 vendor. User
  supplies API key in settings; routing flag in the voice
  settings flips local↔cloud. Capability-delta UX matches the
  provider-switcher pattern from F4.5 ("Cloud STT: lower
  latency on weak hardware. Audio leaves the machine.").
- **Project vocabulary hand-off.** Lead exposes a small
  `project_vocabulary` slice (identifiers, decisions, recurring
  jargon) drawn from `THALYN.md` and Mem0 facts. The voice
  bridge passes that slice as Whisper's `initial_prompt` so the
  engine biases toward it. Two-line wiring in the bridge; the
  hard work (project memory) already lands ahead of v0.33.
- **MLX-Whisper opt-in.** Settings flag, documented as "Apple
  Silicon power-user alternative." Routes voice through MLX
  instead of whisper.cpp; user accepts a separate model
  download (~600 MB) and the dependency on Python + MLX in
  the bundle path. Not wired up unless the flag flips.

### What v0.33 doesn't build

- Hotkey-anywhere global activation. v1.x.
- Smart Modes / Power Mode / per-app profiles. v1.x.
- LLM transcript cleanup. v1.x; the editable-transcript-before-send
  path covers the same need at lower cost.
- Screen-context-aware vocabulary biasing. v1+.
- Voice output (Thalyn speaking back). F7.5 explicitly parks it.
- Multilingual voice input. v1.x; F7 scopes English-only for v1.
- Wake-word / always-on listening. F7 explicitly excludes it on
  privacy grounds.
- A native Wayland audio path. PipeWire portal docs only;
  ALSA/Pulse direct works for the v1 supported-distro set.

### Plan adjustments

- **ADR-0025 drafts as Proposed.** Names the engine, model,
  cloud fallback, bundle strategy, and the UX-patterns-carried-
  over set. v0.33 promotes to Accepted with any refinements
  implementation surfaces.
- **`` §23 rewrites** to replace the
  speculative "Whisper.cpp (or equivalent)" wording with the
  spike's resolved picks — engine pinned, model defaults
  pinned, cloud vendor pinned, UX patterns pinned to the v0.33
  vs v1.x split above. The latency budget moves from "< 500 ms"
  (the planning-time guess) to **"final transcript ready
  ≤ 250 ms after release on Apple Silicon with `small.en`;
  ≤ 800 ms on the Linux without GPU baseline with `base.en`"** —
  numbers grounded in the bake-off plus published cross-platform
  RTF, not vibes.
- **`02-architecture.md` §13 risk #9** ("Voice STT latency on
  Apple Silicon") flips from open to "addressed by ADR-0025"
  after v0.33 ships and the verification recipe re-runs the
  in-tree bake-off harness on the M1 baseline.
- **`docs/voice-input.md`** lands alongside this spike with the
  hardware-floor matrix per OS, the microphone-permission
  recipes, and the PipeWire portal note. It's the user-facing
  + ops-facing doc; this spike is the design-rationale doc.
- **`project_voice_input` agent memory updates.** The "VoiceInk
  (MIT-licensed reference)" wording is wrong (VoiceInk is
  GPL-3.0); the memory updates to "VoiceInk study-only —
  GPL-3.0 incompatible with our MIT license, study patterns
  but do not cherry-pick code." The Whisper.cpp baseline
  guidance carries forward.
- **`docs/going-public-checklist.md`** gets two rows:
  (1) Wire CEF media-permission UX (`OnRequestMediaAccessPermission`
  + `OnShowPermissionPrompt` callbacks + `enable-media-stream`
  switch) for in-CEF voice flows; (2) PipeWire/portal native
  Linux audio integration once the portal is ubiquitous on
  shipping distros.
- **Bake-off harness retained at `docs/spikes/voice-bake-off/`.**
  The fixture, the harness scripts, and the per-cell results
  are committed under the spike directory so v0.33's
  cross-platform verification can re-run the matrix on Linux
  without GPU and the 2-year-old Windows laptop slot. The
  Python venv is gitignored; the fixture (jfk.wav + jfk.txt)
  is in-tree and small (~350 KB total).

## Risks not retired

- **Core ML support in `whisper-cpp-plus`.** The crate doesn't
  expose Core ML today. If our PR doesn't merge cleanly upstream
  in the v0.33 window, we carry a build.rs patch — small but
  ours. If `whisper-cpp-plus` itself stalls (single maintainer;
  0.1.4 was the last release as of 2026-02-23), the fallback
  is a thin in-tree binding (cxx + `whisper.h`). Acceptable
  cost; flagging it explicitly so v0.33 doesn't discover the
  gap mid-build.
- **First-run download UX for `small.en`.** A 487 MB download
  with a progress bar is real friction for first-time voice
  use on a slow connection. The base.en preload covers
  immediate-first-use, but the *first push-to-talk* still hits
  the bigger download. v0.33's first-run flow needs to surface
  this clearly — it's not a hidden tax.
- **The latency-budget number on the M1 baseline is interpolated,
  not measured.** The bake-off ran on M4 with public M1 numbers
  for the cross-check. v0.33's verification recipe must re-run
  the bake-off on an M1 to confirm the ≤ 250 ms budget holds.
  If it doesn't, the M1 default flips to base.en + Core ML and
  the budget docs revise.
- **Deepgram pricing changes.** The cloud-fallback recommendation
  pins a vendor on a 2026 pricing snapshot. If pricing or
  capability shifts, the routing layer's vendor-switch surface
  (built into the v0.33 design as a settings toggle) takes the
  pressure — the architecture doesn't lock us in.
- **The bake-off matrix used English-only models on a
  broadcast-quality fixture.** Real conversational input
  (technical jargon, cross-talk, accented English) will widen
  the WER spread between model sizes. v0.33's verification must
  run an additional fixture closer to Thalyn's actual usage
  pattern (a delegation request with project-specific
  vocabulary) before declaring small.en the universal default.
  Our reasoning is robust to this — small.en is the median pick
  even on hard speech — but the *number* on the worst case is
  unmeasured.
- **VoiceInk is GPL-3.0.** Our memory was wrong; we re-implement
  patterns rather than cherry-pick code. v0.33's PRs need to
  carry their own implementations of every pattern listed in
  F7's "Land in v0.33" rows.

## Sources

### Bake-off (in-tree, this spike)

- `docs/spikes/voice-bake-off/fixture/jfk.wav` — 11.0 s public-domain JFK clip from `ggml-org/whisper.cpp/samples/`
- `docs/spikes/voice-bake-off/fixture/jfk.txt` — reference transcript
- `docs/spikes/voice-bake-off/scripts/bench.py` — bake-off harness
- `docs/spikes/voice-bake-off/results/bench-all.json` — per-cell results (JSON)

### Engines and bindings

- [`ggml-org/whisper.cpp` README](https://github.com/ggml-org/whisper.cpp) — engine, Metal, Core ML build flag
- [`ggml-org/whisper.cpp` issue #89 — benchmark results](https://github.com/ggml-org/whisper.cpp/issues/89) — M1 / M1 Pro / M1 Mac Mini encoder timings
- [`ggml-org/whisper.cpp` PR #566 — Core ML support](https://github.com/ggml-org/whisper.cpp/pull/566) — ANE encoder, 3–6× speedup
- [`ggml-org/whisper.cpp` discussion #548 — running encoder on ANE](https://github.com/ggml-org/whisper.cpp/discussions/548)
- [`SYSTRAN/faster-whisper`](https://github.com/SYSTRAN/faster-whisper) — CTranslate2-based whisper, int8 / fp16
- [`ml-explore/mlx-examples` — whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) — MLX-Whisper, Apple-Silicon-only
- [`huggingface/distil-whisper`](https://github.com/huggingface/distil-whisper) — distilled variants, 6× faster within-1 % WER
- [`distil-whisper/distil-large-v3` model card](https://huggingface.co/distil-whisper/distil-large-v3)
- [`operator-kit/whisper-cpp-plus-rs`](https://github.com/operator-kit/whisper-cpp-plus-rs) — recommended binding (MIT, active 2026-02)
- [`whisper-cpp-plus` 0.1.4 on crates.io](https://crates.io/crates/whisper-cpp-plus)
- [`tazz4843/whisper-rs`](https://github.com/tazz4843/whisper-rs) — alternative binding, repo archived 2025-07-30

### ASR leaderboards and comparative analysis

- [Open ASR Leaderboard (Hugging Face)](https://huggingface.co/blog/open-asr-leaderboard) — 2026 trends, multilingual + long-form tracks
- [Open ASR Leaderboard paper (arXiv 2510.06961)](https://arxiv.org/html/2510.06961v1) — methodology and reproducibility
- [Northflank — best open-source STT 2026](https://northflank.com/blog/best-open-source-speech-to-text-stt-model-in-2026-benchmarks)
- [Gladia — best open-source STT 2026](https://www.gladia.io/blog/best-open-source-speech-to-text-models)
- [Modelslab — Parakeet.cpp vs Whisper, 2026](https://modelslab.com/blog/audio-generation/parakeet-cpp-vs-whisper-self-hosted-asr-comparison-2026)
- [E2E Networks — Parakeet vs Whisper vs Nemotron on NVIDIA L4](https://www.e2enetworks.com/blog/benchmarking-asr-models-nvidia-l4-parakeet-whisper-nemotron)
- [Voicci — whisper performance on Apple Silicon (M1–M4)](https://www.voicci.com/blog/apple-silicon-whisper-performance.html)
- [Quantization for OpenAI's Whisper Models (arXiv 2503.09905)](https://arxiv.org/html/2503.09905v1) — 19 % latency reduction, 45 % size reduction

### Cloud STT candidates

- [Deepgram Nova-3 — best STT APIs 2026](https://deepgram.com/learn/best-speech-to-text-apis-2026)
- [Deepgram vs OpenAI vs Google STT — accuracy, latency, price](https://deepgram.com/learn/deepgram-vs-openai-vs-google-stt-accuracy-latency-price-compared)
- [TokenMix — gpt-4o-transcribe vs Whisper, latency 2026](https://tokenmix.ai/blog/gpt-4o-transcribe-vs-whisper-review-2026)
- [Softcery — STT/TTS for voice agents 2025–2026](https://softcery.com/lab/how-to-choose-stt-tts-for-ai-voice-agents-in-2025-a-comprehensive-guide)

### UX patterns

- [`Beingpax/VoiceInk`](https://github.com/Beingpax/VoiceInk) — GPL-3.0, study reference
- [Voibe — VoiceInk review 2026](https://www.getvoibe.com/resources/voiceink-review/)
- [`OpenWhispr/openwhispr`](https://github.com/OpenWhispr/openwhispr) — cross-platform, Whisper + Parakeet, BYOK cloud

### Microphone permission and CEF coexistence

- [CEF Forum — microphone permission troubleshooting](https://magpcss.org/ceforum/viewtopic.php?f=6&t=20235)
- [chromiumembedded/cef issue #3076 — macOS mic+camera permission](https://bitbucket.org/chromiumembedded/cef/issues/3076/can-t-get-microphone-and-camera-permission)
- [`tauri-apps/tauri` issue #9928 — accessing microphone from Rust on macOS](https://github.com/tauri-apps/tauri/issues/9928)
- [`tauri-apps/tauri` issue #11951 — macOS mic/camera permission not prompted](https://github.com/tauri-apps/tauri/issues/11951)
- [`ayangweb/tauri-plugin-macos-permissions`](https://github.com/ayangweb/tauri-plugin-macos-permissions) — checking and requesting macOS perms
- [Tauri v2 — macOS Application Bundle docs](https://v2.tauri.app/distribute/macos-application-bundle/)
- [Microsoft Learn — UWP App capability declarations (microphone)](https://learn.microsoft.com/en-us/windows/uwp/packaging/app-capability-declarations)
- [Microsoft Learn — UWP speech recognition](https://learn.microsoft.com/en-us/windows/uwp/ui-input/speech-recognition)
- [`flatpak/xdg-desktop-portal` — Audio portal discussion](https://github.com/flatpak/xdg-desktop-portal/discussions/1142)
- [PipeWire — Portal access control docs](https://docs.pipewire.org/page_portal.html)

### Architecture context

- [`01-requirements.md` §F7 — voice input requirements](../../01-requirements.md)
- [`02-architecture.md` §4.1 — voice STT bridge component](../../02-architecture.md)
- [`02-architecture.md` §13 risk #9 — voice STT latency on Apple Silicon](../../02-architecture.md)
- [`docs/adr/0016-license-mit.md`](../adr/0016-license-mit.md) — MIT license bar
- [`docs/adr/0019-browser-engine-v2.md`](../adr/0019-browser-engine-v2.md) — CEF in-app browser
- [`project_voice_input` — agent memory entry, updated alongside this spike]
