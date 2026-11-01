#!/usr/bin/env python3
"""Benchmark faster-whisper models/devices/compute types on the local machine.

    source scripts/gpu_env.sh
    python tools/benchmark.py --models tiny base small medium --json benchmark.json
    python tools/benchmark.py --models small --samples samples/ja_sample.wav

Reports load time, VRAM delta, inference latency and real-time factor (RTF =
inference time / audio duration; lower is better). The first transcription of a
process is treated as warm-up and only the second one is reported.
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import statistics
import subprocess
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import numpy as np
from app.config import load_config
from app.stt import WhisperEngine

SAMPLE_RATE = 16000


def read_wav(path: str | Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != SAMPLE_RATE:
            raise SystemExit(f"{path}: expected 16 kHz mono 16-bit WAV")
        frames = wav.readframes(wav.getnframes())
    return np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0


def gpu_memory_mib() -> float | None:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return float(out.strip().splitlines()[0])
    except Exception:
        return None


@dataclass
class Row:
    model: str
    device: str
    compute_type: str
    status: str
    load_s: float | None = None
    vram_mib: float | None = None
    audio_s: float | None = None
    latency_s: float | None = None
    rtf: float | None = None
    language: str | None = None
    language_prob: float | None = None
    segments: int | None = None
    text_chars: int | None = None
    note: str | None = None


async def bench_combo(
    base_config,
    model: str,
    device: Literal["cuda", "cpu"],
    compute_type: str,
    audio: np.ndarray,
    runs: int,
) -> Row:
    config = base_config.model_copy(deep=True)
    config.whisper.model = model
    config.whisper.device = device
    config.whisper.compute_type = compute_type
    config.whisper.allowed_models = [model]

    before = gpu_memory_mib()
    engine = WhisperEngine(config)
    try:
        load_start = time.monotonic()
        spec = await engine.ensure_model(None)
        load_s = time.monotonic() - load_start
        after = gpu_memory_mib()
        vram = (after - before) if (before is not None and after is not None) else None
        if device != "cpu" and spec.device == "cpu":
            return Row(
                model=model,
                device=device,
                compute_type=compute_type,
                status="fallback-cpu",
                load_s=round(load_s, 2),
                note=spec.fallback_reason,
            )

        latencies: list[float] = []
        last = None
        for _ in range(max(1, runs)):
            last = await engine.transcribe(audio)
            latencies.append(last.inference_s)
        assert last is not None
        latency = statistics.median(latencies)
        return Row(
            model=model,
            device=spec.device,
            compute_type=spec.compute_type,
            status="ok",
            load_s=round(load_s, 2),
            vram_mib=round(vram, 0) if vram is not None else None,
            audio_s=round(last.duration_s, 1),
            latency_s=round(latency, 2),
            rtf=round(latency / last.duration_s, 3) if last.duration_s else None,
            language=last.language,
            language_prob=round(float(last.language_probability), 3),
            segments=len(last.segments),
            text_chars=len(last.text),
        )
    except Exception as exc:
        return Row(
            model=model,
            device=device,
            compute_type=compute_type,
            status="error",
            note=f"{type(exc).__name__}: {exc}"[:300],
        )
    finally:
        del engine
        gc.collect()


def to_markdown(rows: list[Row]) -> str:
    header = (
        "| Modèle | Device | compute_type | Statut | Chargement (s) | VRAM (Mio) | "
        "Audio (s) | Latence (s) | RTF | Langue | Segments |"
    )
    lines = [header, "|" + "---|" * 11]
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    row.model,
                    row.device,
                    row.compute_type,
                    row.status,
                    f"{row.load_s:.2f}" if row.load_s is not None else "-",
                    f"{row.vram_mib:.0f}" if row.vram_mib is not None else "-",
                    f"{row.audio_s:.1f}" if row.audio_s is not None else "-",
                    f"{row.latency_s:.2f}" if row.latency_s is not None else "-",
                    f"{row.rtf:.3f}" if row.rtf is not None else "-",
                    f"{row.language} ({row.language_prob:.2f})"
                    if row.language and row.language_prob is not None
                    else "-",
                    str(row.segments) if row.segments is not None else "-",
                ]
            )
            + " |"
        )
    return "\n".join(lines)


async def main(args: argparse.Namespace) -> int:
    base_config = load_config(args.config)
    samples = [read_wav(path) for path in args.samples]
    combos: list[tuple[str, Literal["cuda", "cpu"], str]] = []
    devices: list[tuple[Literal["cuda", "cpu"], list[str]]] = [
        ("cuda", args.gpu_compute_types),
        ("cpu", args.cpu_compute_types),
    ]
    for model in args.models:
        for device, compute_types in devices:
            for compute_type in compute_types:
                combos.append((model, device, compute_type))

    rows: list[Row] = []
    for model, device, compute_type in combos:
        for index, audio in enumerate(samples):
            label = f"{model}/{device}/{compute_type} sample{index + 1}"
            print(f"→ {label} …", flush=True)
            row = await bench_combo(base_config, model, device, compute_type, audio, runs=args.runs)
            if len(samples) > 1:
                row.note = (row.note or "") + f" sample={Path(args.samples[index]).name}"
            rows.append(row)
            print(
                f"  {row.status} latency={row.latency_s}s rtf={row.rtf} vram={row.vram_mib}",
                flush=True,
            )

    print()
    print(to_markdown(rows))
    if args.json:
        payload = json.dumps([asdict(row) for row in rows], indent=2)
        await asyncio.to_thread(Path(args.json).write_text, payload)
        print(f"\nJSON written to {args.json}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=None, help="server config.yaml")
    parser.add_argument("--models", nargs="+", default=["tiny", "base", "small", "medium"])
    parser.add_argument(
        "--gpu-compute-types", nargs="+", default=["int8_float32", "int8", "float32"]
    )
    parser.add_argument("--cpu-compute-types", nargs="+", default=["int8"])
    parser.add_argument(
        "--samples", nargs="+", default=["samples/ja_sample.wav"], help="16 kHz mono WAV files"
    )
    parser.add_argument("--runs", type=int, default=2, help="transcriptions per combo (median)")
    parser.add_argument("--json", default=None, help="write raw results to this file")
    return parser


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(build_parser().parse_args())))
