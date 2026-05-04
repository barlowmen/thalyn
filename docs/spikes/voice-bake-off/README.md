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
│   ├── jfk.wav    — 11.0 s public-domain JFK clip from ggml-org/whisper.cpp/samples/
│   └── jfk.txt    — reference transcript for WER
├── scripts/
│   ├── bench.py     — full bake-off (whisper.cpp + faster-whisper + mlx-whisper)
│   └── bench_mlx.py — re-runs only the mlx-whisper rows
├── results/         — per-cell JSON output, one file per (engine, model)
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
- The fixture is the same one whisper.cpp's own benchmarks have
  used for years; ranking against it is comparable across the
  ecosystem.
- Adding a longer / more conversational fixture is the v0.33
  verify-step's job — broadcast-quality JFK is enough to *rank*
  engines but not to characterize accuracy on the EM-conversation
  shape.
