#!/usr/bin/env python3
"""Isolated synthetic Session benchmark: no production server, network, or model required."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import re
import statistics
import time
from pathlib import Path
from typing import Any

import numpy as np
from app.config import Config
from app.session import Session
from app.stt import ModelSpec, Segment, Transcription
from app.telemetry import measure


class Collect(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.values: dict[str, list[float]] = {}

    def emit(self, record: logging.LogRecord) -> None:
        match = re.fullmatch(r"stage=(\w+) duration_ms=([0-9.]+)", record.getMessage())
        if match:
            self.values.setdefault(match[1], []).append(float(match[2]))


class FakeEngine:
    def __init__(self, delay: float) -> None:
        self.delay = delay
        self.sem = asyncio.Semaphore(1)
        self.spec = ModelSpec("synthetic", "cpu", "int8")

    async def transcribe(self, audio, **kwargs):
        with measure("stt_wait"):
            await self.sem.acquire()
        try:
            with measure("stt_inference"):
                await asyncio.sleep(self.delay)
            return Transcription(
                [Segment(0, len(audio) / 16000, "synthetic")],
                "en",
                1.0,
                len(audio) / 16000,
                self.delay,
                self.spec,
            )
        finally:
            self.sem.release()


class FakeTranslator:
    def __init__(self, delay: float) -> None:
        self.delay = delay

    def translate(self, text, source, target):
        time.sleep(self.delay)
        return "synthetic"


def stats(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {
        "median": round(statistics.median(ordered), 3),
        "p95": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 3),
    }


async def one_run(delay: float, frames: int, frame_ms: int, limit_s: float) -> dict:
    config = Config.model_validate(
        {"server": {"token": "synthetic-test-token-1234"}, "vad": {"max_segment_s": 1.0}}
    )
    session = Session(config, FakeEngine(delay), FakeTranslator(delay))  # type: ignore[arg-type]
    session.set_live_config(partials=False, target_lang="fr")

    async def vad(audio):
        await asyncio.sleep(delay)
        return np.ones(max(1, audio.size // 512), dtype=np.float32)

    session._vad_probs = vad  # type: ignore[method-assign]
    queue: asyncio.Queue[tuple[float, bytes] | None] = asyncio.Queue()
    peaks = [0]
    queue_samples: list[int] = []
    ages: list[float] = []
    frame = np.zeros(16000 * frame_ms // 1000, dtype="<i2").tobytes()
    start = time.monotonic()

    async def producer() -> None:
        for _ in range(frames):
            await queue.put((time.monotonic(), frame))
            peaks[0] = max(peaks[0], queue.qsize())
            queue_samples.append(queue.qsize())
            await asyncio.sleep(frame_ms / 1000)
        await queue.put(None)

    async def consumer() -> None:
        while (item := await queue.get()) is not None:
            created, data = item
            ages.append((time.monotonic() - created) * 1000)
            session.accept_audio(data)
            await session.process()
        await session.finalize()

    try:
        await asyncio.wait_for(asyncio.gather(producer(), consumer()), limit_s)
        elapsed = time.monotonic() - start
        return {
            "elapsed_ms": elapsed * 1000,
            "rtf": elapsed / (frames * frame_ms / 1000),
            "queue_peak_frames": peaks[0],
            "queue_age_ms": stats(ages),
            "queue_samples_frames": queue_samples,
            "error": None,
        }
    except TimeoutError:
        return {
            "elapsed_ms": limit_s * 1000,
            "rtf": None,
            "queue_peak_frames": peaks[0],
            "queue_age_ms": stats(ages),
            "queue_samples_frames": queue_samples,
            "error": "timeout",
        }
    except Exception as exc:
        return {
            "elapsed_ms": None,
            "rtf": None,
            "queue_peak_frames": peaks[0],
            "queue_age_ms": stats(ages),
            "queue_samples_frames": queue_samples,
            "error": type(exc).__name__,
        }


async def main(args: argparse.Namespace) -> None:
    os.environ["AUDIO_LENS_METRICS"] = "1"
    logger = logging.getLogger("audiolens.metrics")
    logger.setLevel(logging.INFO)
    report: dict[str, Any] = {
        "kind": "isolated synthetic Session; no Android or WebSocket",
        "scenarios": {},
    }
    for label, delay in (("normal", args.normal_ms / 1000), ("ralenti", args.slow_ms / 1000)):
        collector = Collect()
        logger.addHandler(collector)
        runs = [
            await one_run(delay, args.frames, args.frame_ms, args.timeout) for _ in range(args.runs)
        ]
        logger.removeHandler(collector)
        report["scenarios"][label] = {
            "runs": len(runs),
            "delay_ms_per_fake_stage": delay * 1000,
            "elapsed_ms": stats([r["elapsed_ms"] for r in runs if r["elapsed_ms"] is not None]),
            "rtf": stats([r["rtf"] for r in runs if r["rtf"] is not None]),
            "queue_peak_frames": stats([r["queue_peak_frames"] for r in runs]),
            "metrics_ms": {name: stats(values) for name, values in collector.values.items()},
            "queue_samples_frames": [r["queue_samples_frames"] for r in runs],
            "queue_age_ms": [r["queue_age_ms"] for r in runs],
            "errors": sum(r["error"] is not None for r in runs),
            "timeouts": sum(r["error"] == "timeout" for r in runs),
        }
    output = json.dumps(report, indent=2, ensure_ascii=False)
    if args.json:
        await asyncio.to_thread(Path(args.json).write_text, output + "\n")
    if args.markdown:
        lines: list[str] = [
            "# Benchmark synthétique isolé",
            "",
            report["kind"],
            "",
            "| Scénario | Durée médiane / p95 (ms) | RTF médian / p95 | "
            "File max médiane / p95 (trames) | Erreurs / délais |",
            "|---|---:|---:|---:|---:|",
        ]

        def pair(data, key):
            values = data[key]
            return f"{values['median']} / {values['p95']}" if values else "—"

        for name, data in report["scenarios"].items():
            lines.append(
                f"| {name} | {pair(data, 'elapsed_ms')} | {pair(data, 'rtf')} | "
                f"{pair(data, 'queue_peak_frames')} | {data['errors']} / {data['timeouts']} |"
            )
        lines.extend(
            [
                "",
                "Les métriques VAD, STT, attente et traduction et la série de file "
                "sont dans le JSON ; toutes sont simulées.",
            ]
        )
        await asyncio.to_thread(Path(args.markdown).write_text, "\n".join(lines) + "\n")
    print(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--frames", type=int, default=12)
    parser.add_argument("--frame-ms", type=int, default=100)
    parser.add_argument("--normal-ms", type=float, default=5)
    parser.add_argument("--slow-ms", type=float, default=150)
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--json")
    parser.add_argument("--markdown")
    parsed = parser.parse_args()
    if min(parsed.runs, parsed.frames, parsed.frame_ms, parsed.timeout) <= 0:
        parser.error("runs, frames, frame-ms and timeout must be positive")
    asyncio.run(main(parsed))
