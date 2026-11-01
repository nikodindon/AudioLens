#!/usr/bin/env python3
"""Compare full-prefix Silero VAD with an isolated stateful candidate; no server."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
from faster_whisper.vad import get_vad_model

from tools.benchmark_whisper_ab import make_audio

WINDOW, CONTEXT = 512, 64


def stats(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {
        "median": round(statistics.median(ordered), 4),
        "p95": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 4),
    }


class IncrementalCandidate:
    """Experiment only: retain ONNX recurrent state and recompute the newest window."""

    def __init__(self, model: Any) -> None:
        self.model = model
        self.h = np.zeros((1, 1, 128), dtype=np.float32)
        self.c = np.zeros((1, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, CONTEXT), dtype=np.float32)
        self.done = 0
        self.scores: list[float] = []

    def step(self, frame: np.ndarray, h: np.ndarray, c: np.ndarray, context: np.ndarray):
        inp = np.concatenate((context, frame.reshape(1, -1)), axis=1)
        return self.model.session.run(None, {"input": inp, "h": h, "c": c})

    def probabilities(self, audio: np.ndarray) -> np.ndarray:
        n = len(audio) // WINDOW
        if n == 0:
            return np.zeros(0, dtype=np.float32)
        while self.done < n - 1:
            frame = audio[self.done * WINDOW : (self.done + 1) * WINDOW]
            out, self.h, self.c = self.step(frame, self.h, self.c, self.context)
            self.scores.append(float(np.asarray(out).reshape(-1)[0]))
            self.context = frame[-CONTEXT:].reshape(1, -1).copy()
            self.done += 1
        last = audio[(n - 1) * WINDOW : n * WINDOW].copy()
        # faster-whisper's batch preprocessing zeroes the last window's tail.
        last[-CONTEXT:] = 0
        out, _, _ = self.step(last, self.h.copy(), self.c.copy(), self.context.copy())
        return np.asarray([*self.scores, float(np.asarray(out).reshape(-1)[0])])


def run_trial(model: Any, audio: np.ndarray, frame_ms: int) -> dict[str, Any]:
    candidate = IncrementalCandidate(model)
    costs: dict[str, list[float]] = {"full_ms": [], "incremental_ms": []}
    buckets: dict[str, list[float]] = {}
    changes = 0
    max_delta = 0.0
    prefixes = 0
    final_full = np.array([], dtype=bool)
    final_candidate = np.array([], dtype=bool)
    chunk = frame_ms * 16
    for end in range(chunk, len(audio) + chunk, chunk):
        prefix = audio[: min(end, len(audio))]
        usable = len(prefix) // WINDOW * WINDOW
        if not usable:
            continue
        started = time.process_time()
        baseline = np.asarray(model(prefix[:usable].copy(), WINDOW)).reshape(-1)
        full_ms = (time.process_time() - started) * 1000
        started = time.process_time()
        proposed = candidate.probabilities(prefix[:usable])
        incremental_ms = (time.process_time() - started) * 1000
        costs["full_ms"].append(full_ms)
        costs["incremental_ms"].append(incremental_ms)
        bucket = str(math.ceil(usable / 16000))
        buckets.setdefault(bucket, []).append(full_ms)
        final_full, final_candidate = baseline >= 0.5, proposed >= 0.5
        changes += int(np.count_nonzero(final_full != final_candidate))
        max_delta = max(max_delta, float(np.max(np.abs(baseline - proposed))))
        prefixes += 1
    return {
        "frame_ms": frame_ms,
        "prefixes": prefixes,
        "threshold_mismatches": changes,
        "final_flags_equal": bool(np.array_equal(final_full, final_candidate)),
        "full_flags_sha256": hashlib.sha256(final_full.tobytes()).hexdigest(),
        "candidate_flags_sha256": hashlib.sha256(final_candidate.tobytes()).hexdigest(),
        "max_probability_difference": max_delta,
        "full_ms": stats(costs["full_ms"]),
        "incremental_ms": stats(costs["incremental_ms"]),
        "full_total_cpu_ms": sum(costs["full_ms"]),
        "incremental_total_cpu_ms": sum(costs["incremental_ms"]),
        "full_ms_by_accumulated_second": {k: stats(v) for k, v in buckets.items()},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--frame-ms", type=int, choices=[20, 50, 100], default=100)
    parser.add_argument("--silence-ms", type=int, default=200)
    parser.add_argument("--output", type=Path, default=Path("/tmp/audiolens-vad-ab.json"))
    args = parser.parse_args()
    if args.runs < 1 or args.silence_ms < 0:
        parser.error("runs must be positive and silence nonnegative")
    os.environ["OMP_NUM_THREADS"] = "1"
    with tempfile.TemporaryDirectory(prefix="audiolens-vad-ab-") as tmp:
        voice = make_audio(Path(tmp) / "voice.wav")
        audio = np.concatenate([voice, np.zeros(args.silence_ms * 16, dtype=np.float32), voice])[
            :80000
        ]
        model = get_vad_model()
        model(audio[:WINDOW].copy(), WINDOW)  # warm-up
        trials = [run_trial(model, audio, args.frame_ms) for _ in range(args.runs)]
    result = {
        "kind": "isolated Silero experiment, not production",
        "runs": args.runs,
        "audio_s": len(audio) / 16000,
        "frame_ms": args.frame_ms,
        "silence_ms": args.silence_ms,
        "threshold_mismatches": sum(t["threshold_mismatches"] for t in trials),
        "equal_final_runs": sum(t["final_flags_equal"] for t in trials),
        "full_total_cpu_ms": stats([t["full_total_cpu_ms"] for t in trials]),
        "incremental_total_cpu_ms": stats([t["incremental_total_cpu_ms"] for t in trials]),
        "trials": trials,
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({k: v for k, v in result.items() if k != "trials"}, indent=2))


if __name__ == "__main__":
    main()
