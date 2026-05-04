# Voice input

Thalyn's voice input is **local-first by default**: a bundled
Whisper.cpp engine runs on the user's machine and the audio never
leaves the device unless the user opts in to cloud STT. Cloud
STT (Deepgram Nova-3 in v1) is an opt-in alternative for users
who want sub-300 ms streaming latency on hardware that can't run
the local engine interactively.

The full design rationale, including the bake-off that picked
the engine and model defaults, lives at
[`docs/spikes/voice-integration.md`](spikes/voice-integration.md).
This page is the user-facing + ops-facing reference: hardware
requirements, model defaults, microphone-permission setup, and
the cloud-fallback flow.

## Engine and model defaults

| Component       | Default                                                    | Why                                                       |
|-----------------|------------------------------------------------------------|-----------------------------------------------------------|
| Local engine    | Whisper.cpp (via the `whisper-cpp-plus` Rust binding)      | Cross-platform, MIT-licensed, small binary, Metal + Core ML on Apple Silicon, OpenBLAS on x86. |
| Default model   | `small.en` (487 MB)                                        | Sweet spot of accuracy vs latency on Apple Silicon and modern x86. |
| Low-RAM model   | `base.en` (148 MB)                                         | Auto-selected when the on-launch hardware probe says small.en won't run interactively. |
| Apple-Silicon power-user opt-in | MLX-Whisper (matching English model)         | ~3× faster than whisper.cpp + Metal; Apple Silicon only. |
| Cloud fallback  | Deepgram Nova-3 (opt-in; user-supplied API key)            | Sub-300 ms streaming latency; fits the push-to-talk flow. |

The default routing: local Whisper.cpp on every supported
platform; the user opts in to MLX or Deepgram in settings.

## Hardware floor

Voice input is interactive (push-to-talk, < 1 s from release to
final transcript) on the configurations marked **interactive**
below. **Workable** means voice runs but the user feels the
latency on a 5–15 s utterance. **Not supported** means the
engine runs but is too slow to use as a primary input modality;
cloud STT is the recommended path on these.

### Apple Silicon (Mac)

- **M1 / M2 / M3 / M4, 16 GB+, Core ML enabled** —
  **interactive.** small.en at ~ 0.03–0.05× RTF; final
  transcript ready ≤ 250 ms after release on a 5–10 s
  utterance.
- **M1 baseline (8 GB), Core ML enabled** — **interactive
  with base.en**, **workable with small.en**. The hardware
  probe picks base.en by default; the user can override.
- **Intel Mac** — **not supported.** Same posture as
  [`docs/local-models.md`](local-models.md): v1 doesn't ship
  on Intel Mac. Cloud STT is the only path.

### Linux

- **Modern x86 with AVX2, 4+ cores, 8 GB+ RAM** —
  **interactive with base.en** (~ 0.5–1.0× RTF); **workable
  with small.en** (~ 1.5–3.0× RTF). The hardware probe picks
  base.en.
- **Older x86 (no AVX2) or pre-2013** — **workable with
  tiny.en**; the cloud fallback is the practical path.
- **NVIDIA + CUDA** — works (whisper.cpp's `cuda` build
  flag); not the v1 default because the user base for "Linux
  desktop with NVIDIA GPU and no Apple device" overlaps
  mostly with users who would already prefer cloud STT.
- **PipeWire / xdg-desktop-portal** — Linux audio works via
  ALSA / PulseAudio without an explicit prompt on most
  shipping distros today. The portal-based audio
  permission flow is documented but not yet gated behind —
  see "Microphone permission" below.

### Windows

- **2-year-old laptop, Ryzen 5 / i5 with AVX2, 8 GB+** —
  **interactive with base.en**, **workable with small.en**.
  Same shape as the modern Linux x86 row.
- **Older Windows / no AVX2** — **workable with tiny.en**;
  cloud fallback recommended.

The on-launch hardware probe runs a built-in fixture against
each candidate model and picks the largest model that hits a
target RTF. The user can override the probe's pick in settings.

## First-run flow

1. Installer brings down the app + the brain sidecar +
   `base.en` (148 MB). Total bundle ~1.0 GB on macOS, similar
   shape on Windows / Linux.
2. First time the user pushes to talk in the composer, Thalyn
   verifies microphone permission (see below) and runs the
   hardware probe in the background.
3. If the probe picks `small.en`, Thalyn lazy-downloads the
   model (487 MB) with a progress UI in the composer. The user
   can dismiss and stay on `base.en`; voice input still works
   immediately on the preloaded model.
4. On Apple Silicon, the matching `.mlmodelc` artifact
   downloads alongside the `.bin`. The first ANE compile pass
   takes 10–15 s on first transcription; subsequent runs are
   cached.

## Push-to-talk and continuous-listen

- **Default: push-to-talk in the composer.** Hold space (or
  click-and-hold the mic button) to record; release to drop
  the final transcript into the composer for editing;
  Cmd/Ctrl-Enter sends.
- **Level meter** while recording — inline in the composer mic
  affordance.
- **Editable transcript before send.** Always. Voice input is a
  faster way to dictate intent; you stay in control of what
  Thalyn sees.
- **Continuous-listen mode (opt-in).** A toggle in settings
  flips the composer mic into a VAD-segmented continuous-listen
  shape: speech is auto-finalized after a configurable silence
  threshold (default 1.2 s) and the transcript drops into the
  composer. Cmd/Ctrl-Enter still sends; the user can edit
  between pauses.

Wake-word / always-on listening is **not** in v1 scope on
privacy grounds (F7).

## Direct lead chat

Voice input flows through the same composer in direct lead
chat (F2.4) — the lead's voice flow biases against the
project's vocabulary (identifiers, decisions, recurring jargon)
via Whisper's `initial_prompt` shape, derived from the
project's memory tier. The brain composer falls back to
personal-memory-derived vocabulary when the conversation
isn't pinned to a project.

## Cloud STT (opt-in)

Cloud STT lives behind a settings toggle. Enabling it asks the
user for a Deepgram API key, which Thalyn stores in the OS
keychain (the same path as every other secret —
[`docs/adr/0012-provider-abstraction.md`](adr/0012-provider-abstraction.md)).
With cloud STT enabled, voice input routes to Deepgram Nova-3
over a streaming WebSocket; latency is typically 200–400 ms
from end-of-utterance to final transcript.

When cloud STT is on, the composer mic surfaces a
**capability-delta** banner the first time it's used:
"Audio leaves your machine. Lower latency on weak hardware."
The user dismisses or rolls back from this banner — the
posture matches F4.5 (capability-delta UX for provider
switches).

OpenAI `gpt-4o-transcribe` is a known v1.x alternative for
users in the OpenAI auth topology; the cloud-fallback design
parameterizes the vendor so swapping is a settings flip, not a
re-architecture.

## Microphone permission

### macOS

- `NSMicrophoneUsageDescription` is set in the bundle's merged
  `Info.plist`. The first push-to-talk fires the system
  permission prompt; user grants once, never sees it again.
- Known landmine: signed bundles with the key missing from
  the *merged* Info.plist (Tauri merges
  `src-tauri/Info.plist` with its generated one) silently
  fail to prompt. The bundle smoke check verifies the merged
  plist has the key before signing.
- macOS denies microphone access *silently* once the user has
  said "Don't Allow" — `cpal::build_input_stream` succeeds but
  no PCM frames flow. The composer can't classify this as a
  permission failure on the same code path Windows uses; the
  ``open_mic_settings`` Tauri command is wired and points at
  Security & Privacy → Microphone for users who realise they
  denied access by accident.

### Windows

- Tauri's installer formats (MSI, NSIS) ship as legacy
  desktop apps, which means the user must ensure
  Settings → Privacy & security → Microphone →
  *"Let desktop apps access your microphone"* is on. Most
  modern Windows setups have this enabled by default, but a
  fresh install or a hardened-corporate image may not.
- Thalyn surfaces a Settings deep-link (`ms-settings:privacy-microphone`)
  in the composer if the first audio-stream open returns
  access-denied. One click takes the user to the right page.

### Linux

- Most distros expose the input device through ALSA or
  PulseAudio without an explicit prompt; voice input "just
  works" on a typical install.
- PipeWire + `xdg-desktop-portal`'s Audio portal is the
  forward-looking path (matches the camera-portal flow and
  works for sandboxed clients). v1 documents this but
  doesn't gate behind it; the going-public checklist
  carries a row to wire the portal path before public
  release.
- The renderer's "Open settings" affordance returns an error
  on Linux today (no canonical privacy pane to deep-link to);
  the composer leaves the dismiss button so the user can
  resolve the access denial in their distro's audio control
  panel and try again. PipeWire portal integration will
  surface a real deep-link once it lands.

## Privacy posture

- The local-default path keeps audio on the machine.
  Whisper.cpp processes PCM frames in-process; nothing
  hits disk except the user's editable transcript (which
  the user is going to send anyway).
- Cloud STT (when opt-in) leaves the machine. The
  capability-delta banner makes that explicit on first use.
  The Deepgram path uses the user's own API key —
  Thalyn doesn't proxy.
- No wake-word listening; no "always on" capture; no
  background recording.
- Push-to-talk is the default activation pattern.
  Continuous-listen is opt-in and shows a recording
  indicator while active.

## See also

- [`docs/spikes/voice-integration.md`](spikes/voice-integration.md) — full design rationale and bake-off
- [`docs/adr/0025-voice-input-stt.md`](adr/0025-voice-input-stt.md) — the engine + model + cloud decision
- [`docs/local-models.md`](local-models.md) — sibling local-default doc for LLM inference
- [`docs/going-public-checklist.md`](going-public-checklist.md) — CEF media-permission UX + PipeWire portal rows
