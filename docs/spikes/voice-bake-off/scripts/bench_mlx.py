"""Re-run only the mlx-whisper portion of the bake-off (after ffmpeg install)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from bench import (  # noqa: E402
    RESULTS,
    bench_one,
    run_mlx_whisper,
)


def main() -> int:
    RESULTS.mkdir(exist_ok=True)
    out: list[dict] = []
    cases = (
        ("mlx-whisper", "tiny.en", "mlx-community/whisper-tiny.en-mlx"),
        ("mlx-whisper", "base.en", "mlx-community/whisper-base.en-mlx"),
        ("mlx-whisper", "small.en", "mlx-community/whisper-small.en-mlx"),
    )
    for engine, model, repo in cases:
        try:
            rec = bench_one(engine, model, run_mlx_whisper, repo)
        except Exception as exc:  # noqa: BLE001
            print(f"    ERROR: {exc}")
            rec = {"engine": engine, "model": model, "error": str(exc)}
        out.append(rec)
        (RESULTS / f"bench-{engine}-{model}.json").write_text(json.dumps(rec, indent=2))

    print()
    print("MLX summary:")
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
