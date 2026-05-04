# 0025 — Voice input: Whisper.cpp local-first STT with Deepgram cloud fallback

- **Status:** Proposed
- **Date:** 2026-05-03
- **Deciders:** Barlow
- **Supersedes:** —
- **Superseded by:** —

## Context

F7 in `01-requirements.md` commits Thalyn to **voice input as a v1
baseline**, with a **local-first STT default** and an opt-in cloud
alternative. The planning-time guidance pointed at "Whisper.cpp
(or equivalent)" without an empirical bake-off; this ADR resolves
that "(or equivalent)" to a concrete pick before the v0.33 build
commits to a binding, a model, an IPC surface, and a bundle layout.

The decision space is constrained by:

- **MIT-license bar** (ADR-0016) — GPL-licensed engines and
  bindings can be studied for patterns but not cherry-picked.
- **Privacy posture** (NFR4) — local-default, audio stays on the
  machine, cloud STT is opt-in with a capability-delta banner.
- **Cross-platform parity** (Tauri 2 — ADR-0001) — engine must
  ship on macOS, Linux, and Windows from one codebase.
- **Latency budget** — push-to-talk users need final transcript
  in well under a second on Apple Silicon. The
  `02-architecture.md` §13 risk #9 placeholder of "< 500 ms"
  needed to be either confirmed or revised against measured
  numbers.
- **Hardware floor** — F7 implies a device that can run Whisper
  locally; the floor needs to be a real spec (M1 baseline,
  Linux without GPU, 2-year-old Windows laptop), not a wave.

The full bake-off, design rationale, and the in-tree fixture +
harness live at
[`docs/spikes/voice-integration.md`](../spikes/voice-integration.md);
this ADR is the durable decision record. The user-facing +
ops-facing reference for voice input lives at
[`docs/voice-input.md`](../voice-input.md).

## Decision

Adopt **Whisper.cpp as the local-default STT engine**, integrated
into the Rust core via the **`whisper-cpp-plus`** Rust crate (MIT,
active 2026-02), with **Core ML acceleration on Apple Silicon**
(via the upstream `WHISPER_COREML=1` build flag, exposed as a
cargo feature in our build) and **CPU + OpenBLAS** on Linux and
Windows. The crate's `WhisperStream` / `WhisperStreamPcm` types
back the streaming hot path; the `EnhancedWhisperVadProcessor`
(Silero VAD) backs opt-in continuous-listen.

The default model is **`small.en`**. A **`base.en`** lazy-fallback
is auto-selected by an on-launch hardware probe when the platform
fails to hit the latency budget on `small.en`. **`tiny.en`** is
the floor for pre-AVX2 x86 / weak Windows laptops; selectable, not
default.

The bundle layout: **preload `base.en` (148 MB) in the installer**
for immediate first-use; **lazy-download `small.en` (487 MB) on
first push-to-talk** with a progress UI in the composer; on Apple
Silicon, fetch the matching `.mlmodelc` artifact alongside the
`.bin` so the ANE encoder path is available without a separate
download step.

The opt-in cloud fallback vendor is **Deepgram Nova-3** — chosen
for sub-300 ms streaming latency (the only candidate that hits
that target reliably in 2026). Routing is a settings flag; the
user supplies a Deepgram API key, stored via the existing OS
keychain path (ADR-0012). OpenAI `gpt-4o-transcribe` is filed as
a documented v1.x alternative for users in the OpenAI auth
topology — the cloud-fallback design parameterizes the vendor so
swapping is a settings flip, not a re-architecture.

**MLX-Whisper** is filed as a documented opt-in alternative for
Apple Silicon power users (~3× faster than whisper.cpp + Metal
on M-series; Apple-Silicon-only, so not the universal default).
A settings flag flips the routing and triggers a separate model
download (~600 MB) plus the dependency surface for MLX on the
host.

The latency budget moves from the planning-time placeholder of
"< 500 ms" to two grounded numbers:

- **≤ 250 ms** from end-of-utterance to final transcript on
  Apple Silicon with `small.en` and Core ML enabled.
- **≤ 800 ms** on the Linux-without-GPU baseline with `base.en`
  and OpenBLAS.

These are interpolations from the M4 bake-off plus published
M1 / x86 RTF; v0.33's verification recipe re-runs the in-tree
bake-off harness on each platform's representative hardware
(M1 baseline, Linux without GPU, the 2-year-old Windows laptop)
to confirm before declaring exit criteria met.

UX patterns landing in v0.33 (per F7.3 and the spike's
"Land in v0.33" rows): **push-to-talk via hold-space-in-composer**,
**level-meter feedback**, **editable transcript before send**,
**opt-in continuous-listen with VAD-segmented silence-based
auto-send**, and **project-memory-derived vocabulary** passed to
the engine via Whisper's `initial_prompt`. v1.x follow-ups:
hotkey-anywhere, smart modes, power mode, LLM transcript cleanup,
and screen-context awareness — explicitly out of scope for v0.33.

## Consequences

### Positive

- Cross-platform parity from one engine. Whisper.cpp ships on
  macOS, Linux, and Windows from the same crate; no per-platform
  fork in the engine layer. The bake-off harness and fixture
  travel with the engine — re-running on a new platform is a
  single command.
- The CDP architecture analogue: the brain's IPC surface
  (`stt.start` / `stt.chunk` / `stt.stop`) doesn't change
  shape per engine; cloud / MLX / whisper.cpp all sit behind
  it. Provider-style abstraction in the small.
- Privacy posture is honest by default. Audio stays on the
  machine unless the user opts in to cloud, and the
  capability-delta banner makes the trade-off visible at the
  point of opting in.
- `whisper-cpp-plus` is MIT-licensed and active; the binding is
  not a single-source risk in the way archived alternatives are.
- The bake-off matrix (engine × model × platform) is in the
  repo. Every future re-evaluation cycle (per
  `` §7) can re-run it without rebuilding the
  setup.

### Negative

- Core ML support is not yet exposed in `whisper-cpp-plus`. We
  either upstream a `coreml` cargo feature (preferred path) or
  carry a thin `build.rs` patch in our tree. Acceptable cost,
  but it's our cost — flagged so v0.33 doesn't discover the
  gap mid-build.
- A 487 MB lazy-download on first push-to-talk is real friction
  on slow connections. The base.en preload covers
  immediate-first-use, but the *first* push-to-talk on a
  fresh install hits the bigger download. The first-run flow
  needs to surface the cost clearly, not hide it.
- Bundle weight grows by 148 MB (the preloaded `base.en`).
  Combined with the v0.30 CEF cost (+250 MB) and the brain
  sidecar (+263 MB), the installer is ~1.0 GB on macOS.
  Documented; on the going-public-checklist alongside the
  other bundle-weight rows.
- Two engines behind the same flag (whisper.cpp default,
  MLX-Whisper opt-in for Apple Silicon power users) doubles
  the model-distribution path on macOS for the opt-in slice.
  Acceptable because MLX is gated behind a settings flag the
  vast majority of users won't flip.
- The latency budget on the M1 baseline is interpolated, not
  measured. v0.33's verify-step has to re-run the bake-off on
  an M1 to confirm the ≤ 250 ms figure holds before the budget
  is locked in.

### Neutral

- The decision parameterizes the cloud vendor. Pinning Deepgram
  for v1 is a 2026-pricing-snapshot pick; the routing surface
  is built so swapping vendors is a settings change, not an
  architecture rewrite.
- VoiceInk turned out to be GPL-3.0, not MIT as the
  agent-memory entry had claimed. The patterns are
  re-implementable; the code is not. This ADR's pattern picks
  (push-to-talk, level meter, editable transcript,
  continuous-listen, project vocabulary) are independent
  re-implementations.

## Alternatives considered

- **MLX-Whisper as the universal local default.** Faster on
  Apple Silicon (RTF 0.013× at base.en, 0.032× at small.en on
  M4 — vs whisper.cpp + Metal at 0.052× and 0.099×). Rejected
  because MLX is Apple-Silicon-only; recommending it as the
  universal default would fork the engine across platforms and
  add a Linux/Windows fallback for the same job whisper.cpp
  already does cross-platform. Filed as a documented opt-in
  for power users instead.
- **faster-whisper (CTranslate2-based, Python) as the engine.**
  Slowest in the M4 bake-off because it ignores the GPU
  (CTranslate2 has no Metal backend); the int8 quantization
  helps but doesn't close the gap. Adds a Python dependency
  surface inside the Rust core's voice path — wrong direction
  for the architecture (the brain is Python, the core is Rust;
  the STT bridge belongs in the core).
- **Parakeet-TDT 1.1B (NVIDIA NeMo).** Extreme throughput
  (RTFx > 2000 on a GPU) but the 1.1 B-param variant ranks
  ~23rd on the Open ASR Leaderboard for accuracy — explicit
  speed-for-quality trade. Better suited to high-volume
  batch transcription than push-to-talk dictation. The
  CTC variants don't fit the streaming hot path the way
  Whisper does, and the binding story on macOS / Windows is
  weaker than whisper.cpp's. Rejected as the local default;
  filed as one to revisit if the leaderboard ranking shifts.
- **distil-whisper-small.en.** 166 M params, within ~4 % WER
  of large-v3, similar speed to whisper.cpp small.en on
  Apple Silicon. Tempting but most of the distil-Whisper
  wins are at the *large* tier; at the small tier the speed
  parity is close and the accuracy slightly worse. Holding
  with small.en for now; revisit in a quarterly review if
  the distil-small.en quality on conversational English
  improves.
- **OpenAI `gpt-4o-transcribe` as the cloud-fallback default.**
  Highest accuracy in the cloud tier (2.46 % WER on the model
  card); 320 ms latency. Rejected as the *default* because
  Deepgram Nova-3 is purpose-built for streaming voice agents
  and prices lower; filed as a documented v1.x alternative the
  user can opt into.
- **Groq Whisper (large-v3-turbo).** Fast in batch but the LPU
  pipeline is chunk-based rather than true streaming — wrong
  shape for push-to-talk. Out of scope for v0.33.
- **`whisper-rs` (tazz4843) as the binding.** Mature surface;
  rejected because the GitHub repo was archived 2025-07-30 and
  the maintainer isn't shipping further updates. Single-source
  risk on a load-bearing dependency; `whisper-cpp-plus` is
  MIT-licensed, active, and has the streaming + VAD APIs we
  want already.
- **Vendor a thin in-tree binding (cxx + `whisper.h`).**
  Defensible if `whisper-cpp-plus` stalls, but adds binding
  maintenance to our load. Filed as the documented fallback
  if the upstream Core ML PR doesn't merge or the crate stops
  shipping.
- **Building voice patterns directly off VoiceInk's code.**
  Blocked by license — VoiceInk is GPL-3.0, ADR-0016 commits
  to MIT. Patterns are study references; v0.33 implements them
  independently.
- **Wake-word / always-on listening.** F7 explicitly excludes
  on privacy grounds. Push-to-talk + opt-in continuous-listen
  is the v1 surface.
- **Voice output (Thalyn speaking back).** F7.5 parks v1;
  out of scope for this ADR.

## References

- [`01-requirements.md` §F7 — voice input](../../01-requirements.md)
- [`02-architecture.md` §4.1 — voice STT bridge](../../02-architecture.md)
- [`02-architecture.md` §13 risk #9 — Apple Silicon STT latency](../../02-architecture.md)
- [`docs/spikes/voice-integration.md`](../spikes/voice-integration.md) — the bake-off + design rationale
- [`docs/voice-input.md`](../voice-input.md) — user-facing reference
- [ADR-0012 — provider abstraction](0012-provider-abstraction.md) — keychain path for the Deepgram API key
- [ADR-0016 — License: MIT](0016-license-mit.md)
- [ADR-0019 — Browser engine: bundled Chromium via cef-rs](0019-browser-engine-v2.md) — the CEF + CoreAudio coexistence question this spike resolved
- [`ggml-org/whisper.cpp`](https://github.com/ggml-org/whisper.cpp) — the engine
- [`operator-kit/whisper-cpp-plus-rs`](https://github.com/operator-kit/whisper-cpp-plus-rs) — the recommended binding
- [Deepgram Nova-3 — best STT APIs 2026](https://deepgram.com/learn/best-speech-to-text-apis-2026)
- [Open ASR Leaderboard (Hugging Face)](https://huggingface.co/blog/open-asr-leaderboard) — comparative benchmarks
- [`Beingpax/VoiceInk`](https://github.com/Beingpax/VoiceInk) — UX-pattern study reference (GPL-3.0)
