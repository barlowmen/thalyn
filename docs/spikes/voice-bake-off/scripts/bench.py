"""Bake-off harness for the v0.32 voice-integration spike.

Usage:
    source venv/.venv/bin/activate
    python3 scripts/bench.py

Benchmarks each (engine, model) pair against fixture/jfk.wav.
Reports wall-clock latency, real-time factor (RTF = wall_clock / audio_duration),
peak RSS, and word-error-rate (WER) against fixture/jfk.txt.

Each run is repeated three times; we report the median wall-clock to dampen
the cold-start outlier. The first run also includes model load + Metal kernel
compilation, so it's an honest "first transcription latency" measurement —
sustained RTF on subsequent runs is closer to steady state.

Output: results/bench-<engine>-<model>.json
"""

from __future__ import annotations

import json
import re
import resource
import statistics
import subprocess
import sys
import time
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_WAV = ROOT / "fixture" / "jfk.wav"
FIXTURE_TXT = ROOT / "fixture" / "jfk.txt"
RESULTS = ROOT / "results"
WHISPER_CPP_MODELS = Path("/tmp/whisper-models")

REPEATS = 3


def audio_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / w.getframerate()


def normalize(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def wer(reference: str, hypothesis: str) -> float:
    import jiwer

    return float(jiwer.wer(normalize(reference), normalize(hypothesis)))


def peak_rss_mb() -> float:
    # ru_maxrss on macOS is bytes; on Linux it's KB. We're on macOS here.
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return rss / (1024 * 1024)


def child_peak_rss_mb() -> float:
    rss = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return rss / (1024 * 1024)


def run_whisper_cpp(model: str) -> tuple[float, str, float]:
    """Run whisper-cli; returns (wall_clock_s, transcript, peak_child_rss_mb)."""
    model_path = WHISPER_CPP_MODELS / f"ggml-{model}.bin"
    assert model_path.exists(), f"missing model: {model_path}"
    rss_before = child_peak_rss_mb()
    t0 = time.perf_counter()
    proc = subprocess.run(
        [
            "whisper-cli",
            "-m",
            str(model_path),
            "-f",
            str(FIXTURE_WAV),
            "-l",
            "en",
            "-nt",  # no timestamps in output
            "-of",
            "/dev/null",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    elapsed = time.perf_counter() - t0
    rss_delta = max(0.0, child_peak_rss_mb() - rss_before)
    # whisper-cli prints transcript on stdout, with leading whitespace lines
    transcript = " ".join(
        line.strip() for line in proc.stdout.splitlines() if line.strip()
    )
    return elapsed, transcript, rss_delta


def run_faster_whisper(model: str) -> tuple[float, str, float]:
    """Run faster-whisper in-process; returns (wall_clock_s, transcript, peak_rss_mb)."""
    from faster_whisper import WhisperModel

    rss_before = peak_rss_mb()
    t0 = time.perf_counter()
    fw_model = WhisperModel(model, device="cpu", compute_type="int8")
    segments, _ = fw_model.transcribe(str(FIXTURE_WAV), language="en", beam_size=5)
    transcript = " ".join(s.text.strip() for s in segments)
    elapsed = time.perf_counter() - t0
    rss_delta = max(0.0, peak_rss_mb() - rss_before)
    del fw_model
    return elapsed, transcript, rss_delta


def run_mlx_whisper(model_repo: str) -> tuple[float, str, float]:
    """Run mlx-whisper; returns (wall_clock_s, transcript, peak_rss_mb)."""
    import mlx_whisper

    rss_before = peak_rss_mb()
    t0 = time.perf_counter()
    result = mlx_whisper.transcribe(str(FIXTURE_WAV), path_or_hf_repo=model_repo)
    transcript = result["text"].strip()
    elapsed = time.perf_counter() - t0
    rss_delta = max(0.0, peak_rss_mb() - rss_before)
    return elapsed, transcript, rss_delta


def repeat(fn, *args, **kwargs):
    runs = []
    last_transcript = ""
    last_rss = 0.0
    for i in range(REPEATS):
        wall, transcript, rss = fn(*args, **kwargs)
        runs.append(wall)
        last_transcript = transcript
        last_rss = max(last_rss, rss)
        print(f"    run {i + 1}: {wall:.3f}s")
    return runs, last_transcript, last_rss


def bench_one(engine: str, model: str, run_fn, *args) -> dict:
    print(f"[{engine}] model={model}")
    runs, transcript, rss = repeat(run_fn, *args)
    audio_s = audio_duration(FIXTURE_WAV)
    reference = FIXTURE_TXT.read_text().strip()
    cold = runs[0]
    median = statistics.median(runs)
    warm_runs = runs[1:] if len(runs) > 1 else runs
    warm_median = statistics.median(warm_runs)
    rec = {
        "engine": engine,
        "model": model,
        "audio_s": audio_s,
        "runs_s": runs,
        "cold_s": cold,
        "median_s": median,
        "warm_median_s": warm_median,
        "rtf_cold": cold / audio_s,
        "rtf_warm": warm_median / audio_s,
        "rss_mb": rss,
        "transcript": transcript,
        "wer": wer(reference, transcript),
    }
    print(
        f"    cold={cold:.3f}s warm-med={warm_median:.3f}s "
        f"RTF-warm={rec['rtf_warm']:.3f}x WER={rec['wer']:.3f} RSS={rss:.0f}MB"
    )
    return rec


def main() -> int:
    RESULTS.mkdir(exist_ok=True)
    out: list[dict] = []

    cases: list[tuple[str, str, callable, tuple]] = []
    for m in ("tiny.en", "base.en", "small.en"):
        cases.append(("whisper.cpp", m, run_whisper_cpp, (m,)))
    for m in ("tiny.en", "base.en", "small.en"):
        cases.append(("faster-whisper", m, run_faster_whisper, (m,)))
    for repo, label in (
        ("mlx-community/whisper-tiny.en-mlx", "tiny.en"),
        ("mlx-community/whisper-base.en-mlx", "base.en"),
        ("mlx-community/whisper-small.en-mlx", "small.en"),
    ):
        cases.append(("mlx-whisper", label, run_mlx_whisper, (repo,)))

    for engine, model, fn, args in cases:
        try:
            rec = bench_one(engine, model, fn, *args)
        except Exception as exc:  # noqa: BLE001
            print(f"    ERROR: {exc}")
            rec = {"engine": engine, "model": model, "error": str(exc)}
        out.append(rec)
        (RESULTS / f"bench-{engine.replace('.', '_')}-{model}.json").write_text(
            json.dumps(rec, indent=2)
        )

    (RESULTS / "bench-all.json").write_text(json.dumps(out, indent=2))
    print()
    print("Summary:")
    print(
        f"{'engine':<16}{'model':<14}{'cold_s':>8}{'warm_s':>8}{'RTF_warm':>10}{'WER':>8}{'RSS_MB':>10}"
    )
    for rec in out:
        if "error" in rec:
            print(f"{rec['engine']:<16}{rec['model']:<14}  ERROR: {rec['error']}")
            continue
        print(
            f"{rec['engine']:<16}{rec['model']:<14}"
            f"{rec['cold_s']:>8.3f}"
            f"{rec['warm_median_s']:>8.3f}"
            f"{rec['rtf_warm']:>10.3f}"
            f"{rec['wer']:>8.3f}"
            f"{rec['rss_mb']:>10.0f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
