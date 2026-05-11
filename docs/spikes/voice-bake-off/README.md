# Voice STT bake-off harness

Companion artefacts for [`docs/spikes/voice-integration.md`](../voice-integration.md).
The harness measures wall-clock latency, real-time factor (RTF), peak
RSS, and word-error rate (WER) for candidate STT engines on a fixed
audio fixture. It is committed alongside the spike so v0.33's
cross-platform verification step (Linux without GPU, the 2-year-old
Windows laptop slot, M1 baseline) re-uses the same fixture and the
same numbers — and so subsequent re-evaluation cycles can re-run the
matrix without rebuilding the setup.

## Layout

```
voice-bake-off/
├── fixture/
│   ├── jfk.wav         — 11.0 s public-domain JFK clip from ggml-org/whisper.cpp/samples/
│   ├── jfk.txt         — reference transcript for WER
│   ├── delegation.wav  — 16.5 s Piper-TTS synthesis of a delegation request with technical jargon
│   └── delegation.txt  — reference transcript for WER
├── scripts/
│   ├── bench.py     — full bake-off (whisper.cpp + faster-whisper + mlx-whisper, all fixtures)
│   └── bench_mlx.py — re-runs only the mlx-whisper rows
├── results/         — per-cell JSON, one file per (engine, model, fixture)
└── venv/.venv/      — Python venv (gitignored; created on first run)
```

## Re-running the matrix

Prerequisites: `whisper-cli` (e.g. `brew install whisper-cpp` on
macOS), `ffmpeg`, `uv`, and the GGML models in `/tmp/whisper-models/`
(the harness expects `ggml-tiny.en.bin`, `ggml-base.en.bin`,
`ggml-small.en.bin`; download from
`https://huggingface.co/ggerganov/whisper.cpp`).

```sh
# from the bake-off directory
uv venv --python 3.12 venv/.venv
source venv/.venv/bin/activate
uv pip install faster-whisper mlx-whisper 'jiwer<4'
python3 scripts/bench.py
```

The script runs nine (engine, model) pairs (whisper.cpp + faster-
whisper + mlx-whisper × tiny.en + base.en + small.en), each three
times, and writes the median wall-clock + WER + RSS to
`results/bench-<engine>-<model>.json`. A summary table prints to
stdout. mlx-whisper is Apple-Silicon-only and skipped automatically
on other platforms.

## Reproducibility notes

- The whisper.cpp row pays `whisper-cli` per-call process startup
  on every iteration; the warm numbers are an honest *upper bound*
  on what the in-process Rust binding will deliver in production.
- Apple Silicon numbers in the spike are M4 + Metal **without**
  Core ML. With `WHISPER_COREML=1` and the `.mlmodelc` artifact
  alongside the `.bin`, encoder runtime drops 3–6× — the spike's
  cross-check against published M1 numbers covers that path.
- The JFK fixture is the same one whisper.cpp's own benchmarks
  have used for years; ranking against it is comparable across
  the ecosystem.
- The `delegation` fixture is **synthetic**: a Piper-TTS
  rendering (voice `en_US-hfc_female-medium`) of a delegation
  request that exercises the project-vocabulary surface
  (CamelCase identifiers like `WhisperStream`,
  `EnhancedWhisperVadProcessor`, hyphenated names like
  `Lead-Sam`, snake/kebab tokens like `cpal`). Synthetic audio
  is a *baseline* — a real human recording with the same script
  will land before public release; the human-recording row is
  on `docs/going-public-checklist.md`.

## v0.33 re-run on M4 (2026-05-11)

Re-run the matrix on the same M4 / 16 GB box that captured the
v0.32 numbers, this time with both fixtures.

### Latency + WER (warm median, 3 repeats)

| Engine | Model | JFK warm (s) | RTF | JFK WER | Delegation warm (s) | Delegation WER |
|---|---|---:|---:|---:|---:|---:|
| whisper.cpp (Metal)     | tiny.en  | 0.591 | 0.054× | 0.000 | 0.588 | 0.323 |
| whisper.cpp (Metal)     | base.en  | 0.583 | 0.053× | 0.000 | 0.592 | 0.323 |
| whisper.cpp (Metal)     | small.en | 1.100 | 0.100× | 0.000 | **1.089** | **0.161** |
| faster-whisper (CPU i8) | tiny.en  | 0.413 | 0.038× | 0.000 | 0.490 | 0.323 |
| faster-whisper (CPU i8) | base.en  | 0.625 | 0.057× | 0.000 | 0.881 | 0.323 |
| faster-whisper (CPU i8) | small.en | 1.620 | 0.147× | 0.000 | 2.261 | 0.258 |
| MLX-Whisper             | tiny.en  | 0.088 | 0.008× | 0.000 | 0.138 | 0.323 |
| MLX-Whisper             | base.en  | 0.129 | 0.012× | 0.000 | 0.197 | 0.290 |
| MLX-Whisper             | small.en | **0.316** | 0.029× | 0.000 | 0.511 | 0.258 |

### Findings

- **JFK ties at WER=0.000 across all nine cells** — broadcast-
  quality audio is too easy to rank model size. Re-run was
  worth it for the latency refresh but didn't move the WER
  story.
- **Delegation spreads WER as designed.** small.en clearly beats
  tiny/base on technical jargon: whisper.cpp small.en lands at
  WER 0.161 — half the errors of the smaller models on the same
  clip. MLX-Whisper small.en lands at 0.258 (the larger model
  helps, but the smaller-network MLX variant still trips on a
  couple of the more unusual identifiers).
- **The error pattern is exactly the project-vocabulary path's
  job.** Across every cell the misrecognitions cluster on
  identifiers (`LangGraph` → "lane graph", `WhisperStream` →
  "whisper stream", `Lead-Sam` → "lead SAM", `cpal` → "CPAL" /
  "C-PAL"). The bench doesn't pass `initial_prompt`, so the
  table is the *worst-case* WER. Production runs use the
  vocabulary slice from `voice.project_vocabulary`, which folds
  these identifiers into Whisper's prompt and should drop WER
  meaningfully on the same audio.
- **Latency story has a caveat the spike's interpolation
  missed.** ADR-0025's `≤ 250 ms` Apple-Silicon budget assumes
  `small.en + Core ML`. M4 measured numbers without Core ML:
  MLX-Whisper small.en lands at 0.316 s warm (slightly over),
  whisper.cpp + Metal small.en lands at 1.100 s (well over).
  base.en hits the budget on MLX (0.129 s) and gets close on
  whisper.cpp + Metal (0.583 s — about 2.3× the target).
  Core ML wiring (`whisper-cpp-plus` `coreml` feature, on the
  going-public checklist) is the bridge from the current default
  to the spike's budget; the v1 default-engine pick still works
  as a UX (sub-second on small.en), but the headline ADR-0025
  number is honest only with Core ML or MLX selected.
