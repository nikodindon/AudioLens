#!/usr/bin/env python3
"""Compare Whisper generator consumption on the event loop and in a worker.

Uses one local CPU model and locally synthesized speech. No server or network.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import statistics
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np
from faster_whisper import WhisperModel

TEXT = "Hello there. Today the weather is pleasant and sunny."


def distribution(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "median": round(statistics.median(ordered), 3),
        "p95": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 3),
        "max": round(ordered[-1], 3),
    }


def normalized_words(value: str) -> list[str]:
    return re.findall(r"[a-z]+", value.lower())


def word_error(reference: str, candidate: str) -> float:
    ref, hyp = normalized_words(reference), normalized_words(candidate)
    previous = list(range(len(hyp) + 1))
    for i, word in enumerate(ref, 1):
        current = [i]
        for j, other in enumerate(hyp, 1):
            current.append(min(current[-1] + 1, previous[j] + 1, previous[j - 1] + (word != other)))
        previous = current
    return previous[-1] / len(ref) if ref else 0.0


def make_audio(path: Path) -> np.ndarray:
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-threads",
            "1",
            "-f",
            "lavfi",
            "-i",
            f"flite=voice=slt:text='{TEXT}'",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            "-y",
            str(path),
        ],
        check=True,
        timeout=20,
        capture_output=True,
    )
    with wave.open(str(path), "rb") as audio:
        return (
            np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype(np.float32)
            / 32768
        )


async def run_one(
    model: WhisperModel, audio: np.ndarray, *, offload_generator: bool
) -> dict[str, Any]:
    ticks: list[float] = []
    stop = False

    async def heartbeat() -> None:
        nonlocal stop
        next_tick = time.monotonic() + 0.01
        while not stop:
            await asyncio.sleep(max(0, next_tick - time.monotonic()))
            now = time.monotonic()
            ticks.append(max(0.0, (now - next_tick) * 1000))
            next_tick = now + 0.01

    def collect():
        segments, _ = model.transcribe(
            audio,
            language="en",
            beam_size=1,
            temperature=0.0,
            condition_on_previous_text=False,
            vad_filter=False,
        )
        return list(segments)

    watcher = asyncio.create_task(heartbeat())
    await asyncio.sleep(0)
    started, cpu = time.monotonic(), time.process_time()
    if offload_generator:
        segments = await asyncio.to_thread(collect)
    else:
        raw, _ = await asyncio.to_thread(
            model.transcribe,
            audio,
            language="en",
            beam_size=1,
            temperature=0.0,
            condition_on_previous_text=False,
            vad_filter=False,
        )
        segments = list(raw)
    elapsed, cpu_s = time.monotonic() - started, time.process_time() - cpu
    await asyncio.sleep(0.02)
    stop = True
    await watcher
    text = " ".join(segment.text.strip() for segment in segments)
    signature = [(round(s.start, 3), round(s.end, 3), s.text.strip()) for s in segments]
    return {
        "wall_ms": elapsed * 1000,
        "cpu_ms": cpu_s * 1000,
        "loop_lag_max_ms": max(ticks, default=0),
        "loop_lag_p95_ms": distribution(ticks)["p95"],
        "wer": word_error(TEXT, text),
        "segment_count": len(segments),
        "signature_sha256": hashlib.sha256(json.dumps(signature).encode()).hexdigest(),
    }


async def main(model_dir: Path, count: int, output: Path) -> None:
    if not model_dir.is_absolute() or not (model_dir / "model.bin").is_file():
        raise ValueError("expected an absolute cached model directory")
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["OMP_NUM_THREADS"] = "1"
    with tempfile.TemporaryDirectory(prefix="audiolens-whisper-ab-") as tmp:
        audio = make_audio(Path(tmp) / "synthetic.wav")
        model = await asyncio.to_thread(
            WhisperModel,
            str(model_dir),
            device="cpu",
            compute_type="int8",
            cpu_threads=1,
            local_files_only=True,
        )
        await run_one(model, audio, offload_generator=True)  # warm-up
        data: dict[str, list[dict[str, Any]]] = {"baseline": [], "worker": []}
        for index in range(count):
            # Alternating order reduces systematic warm-cache and load bias.
            for mode in ("baseline", "worker") if index % 2 == 0 else ("worker", "baseline"):
                data[mode].append(await run_one(model, audio, offload_generator=mode == "worker"))
        summary = {
            name: {
                key: distribution([r[key] for r in runs])
                for key in ("wall_ms", "cpu_ms", "loop_lag_max_ms", "loop_lag_p95_ms", "wer")
            }
            for name, runs in data.items()
        }
        pairs_equal = sum(
            a["signature_sha256"] == b["signature_sha256"]
            for a, b in zip(data["baseline"], data["worker"], strict=True)
        )
        report = {
            "model": model_dir.parent.parent.name,
            "device": "cpu",
            "compute_type": "int8",
            "cpu_threads": 1,
            "beam_size": 1,
            "sample": "local ffmpeg flite synthetic English",
            "audio_s": len(audio) / 16000,
            "runs_per_mode": count,
            "matched_segment_sequences": pairs_equal,
            "summary": summary,
            "runs": data,
        }
        await asyncio.to_thread(output.write_text, json.dumps(report, indent=2) + "\n")
        print(json.dumps({k: v for k, v in report.items() if k != "runs"}, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--output", type=Path, default=Path("/tmp/audiolens-whisper-ab.json"))
    options = parser.parse_args()
    if options.runs < 1:
        parser.error("--runs must be positive")
    asyncio.run(main(options.model_dir, options.runs, options.output))
