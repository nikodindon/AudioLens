#!/usr/bin/env python3
"""Isolated loopback WebSocket benchmark using offline CPU Whisper and generated speech.

Requires an explicit local faster-whisper model directory and FFmpeg with flite.
Never connects to the configured production server or translator.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.metadata
import json
import math
import os
import re
import resource
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

from websockets.exceptions import ConnectionClosed

try:
    from websockets.asyncio.client import connect

    HEADER_ARGUMENT = "additional_headers"
except ImportError:  # websockets 12
    from websockets.client import connect  # type: ignore[attr-defined,no-redef]

    HEADER_ARGUMENT = "extra_headers"

PHRASES = (
    "Hello there. Today the weather is pleasant and sunny.",
    "The train is leaving soon. Please take your ticket.",
    "We are meeting our friends at the park tomorrow.",
    "A small bird is singing outside the kitchen window.",
    "Thank you for listening. Have a wonderful evening.",
)
TOKEN = "isolated-benchmark-token-not-production"


def summary(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {
        "median": round(statistics.median(ordered), 3),
        "p95": round(ordered[math.ceil(len(ordered) * 0.95) - 1], 3),
        "samples": len(ordered),
    }


def worker(model: str, fd: int) -> None:
    # Applies to this child and its own inference threads only.
    os.nice(19)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["AUDIO_LENS_METRICS"] = "1"
    os.environ["OMP_NUM_THREADS"] = "1"
    import uvicorn
    from app.config import Config
    from app.main import create_app

    config = Config.model_validate(
        {
            "server": {"host": "127.0.0.1", "token": TOKEN, "max_sessions": 1},
            "whisper": {
                "model": model,
                "device": "cpu",
                "compute_type": "int8",
                "cpu_threads": 1,
                "allowed_models": [model],
            },
            "vad": {"threshold": 0.5, "min_silence_ms": 500, "max_segment_s": 5},
            "translation": {"engine": "none", "target_lang": "fr"},
        }
    )
    app = create_app(config=config, config_path="/nonexistent-isolated-benchmark.yaml")
    server = uvicorn.Server(uvicorn.Config(app, log_level="info", access_log=False))
    sock = socket.socket(fileno=fd)
    server.run(sockets=[sock])


def synthesize(directory: Path, length: str = "medium") -> list[Path]:
    files = []
    for index, phrase in enumerate(PHRASES):
        if length == "short":
            phrase = " ".join(phrase.split()[:4])
        elif length == "long":
            phrase = " ".join([phrase] * 3)
        output = directory / f"synthetic-{index}.wav"
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
                f"flite=voice=slt:text='{phrase}'",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                "-y",
                str(output),
            ],
            check=True,
            timeout=20,
            capture_output=True,
        )
        files.append(output)
    return files


async def stream(
    uri: str,
    path: Path,
    timeout_s: float,
    *,
    frame_ms: int = 100,
    silence_ms: int = 1000,
    mode: str = "live",
    partials: bool = False,
) -> dict[str, Any]:
    with wave.open(str(path), "rb") as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, 16000):
            raise ValueError("expected PCM16 mono 16 kHz")
        data = source.readframes(source.getnframes())
    # A silence tail allows normal VAD finalization. It is included in duration.
    data += bytes(32 * silence_ms)
    frame_bytes = 32 * frame_ms
    frames = [data[i : i + frame_bytes] for i in range(0, len(data), frame_bytes)]
    result: dict[str, Any] = {
        "sample": path.name,
        "audio_s": len(data) / 32000,
        "sent_bytes": 0,
        "write_buffer_peak_bytes": 0,
        "finals": [],
        "errors": [],
        "timeout": False,
        "partial_count": 0,
        "done_count": 0,
    }
    options: dict[str, Any] = {HEADER_ARGUMENT: {"Authorization": f"Bearer {TOKEN}"}}
    async with connect(
        uri,
        **options,
        open_timeout=timeout_s,
        close_timeout=3,
    ) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "start",
                    "source_lang": "en",
                    "target_lang": "fr",
                    "translate": False,
                    "partials": partials,
                    "mode": mode,
                }
            )
        )
        ready = json.loads(await asyncio.wait_for(ws.recv(), timeout_s))
        if ready.get("type") != "ready":
            raise RuntimeError("server did not send ready")
        result["model"] = Path(ready["whisper_model"]).name
        result["device"] = ready["whisper_device"]
        result["compute_type"] = ready["whisper_compute_type"]
        start = time.monotonic()
        stop_time: float | None = None

        async def receive() -> None:
            try:
                while True:
                    item = json.loads(await ws.recv())
                    now = time.monotonic()
                    if item.get("type") == "final":
                        # Server segment timestamps are relative and can omit trimmed silence.
                        result["finals"].append(
                            {
                                "segment_id": item.get("segment_id"),
                                "text_sha256": hashlib.sha256(
                                    item.get("text_src", "").encode()
                                ).hexdigest(),
                                "start_ms": item.get("start_ms"),
                                "arrival_ms": round((now - start) * 1000, 3),
                                "end_ms": item["end_ms"],
                                "arrival_minus_segment_end_ms": round(
                                    (now - start) * 1000 - item["end_ms"], 3
                                ),
                                "after_stop_ms": round((now - stop_time) * 1000, 3)
                                if stop_time
                                else None,
                            }
                        )
                    elif item.get("type") == "partial":
                        result["partial_count"] += 1
                    elif item.get("type") == "done":
                        result["done_count"] += 1
                    elif item.get("type") == "error":
                        result["errors"].append(item.get("code", "error"))
            except ConnectionClosed as exc:
                result["close_code"] = exc.code

        receiver = asyncio.create_task(receive())
        try:
            for index, frame in enumerate(frames):
                deadline = start + index * frame_ms / 1000
                await asyncio.sleep(max(0.0, deadline - time.monotonic()))
                await asyncio.wait_for(ws.send(frame), timeout_s)
                result["sent_bytes"] += len(frame)
                result["write_buffer_peak_bytes"] = max(
                    result["write_buffer_peak_bytes"], ws.transport.get_write_buffer_size()
                )
            await asyncio.sleep(max(0.0, start + len(data) / 32000 - time.monotonic()))
            stop_time = time.monotonic()
            await ws.send('{"type":"stop"}')
            await asyncio.wait_for(receiver, timeout_s)
            result["stop_to_close_ms"] = round((time.monotonic() - stop_time) * 1000, 3)
        except TimeoutError:
            result["timeout"] = True
            receiver.cancel()
        finally:
            if not receiver.done():
                receiver.cancel()
    ids = [item["segment_id"] for item in result["finals"]]
    result["duplicate_segment_ids"] = len(ids) - len(set(ids))
    result["out_of_order_ids"] = ids != sorted(ids)
    if mode == "clip" and result["done_count"] != 1:
        result["errors"].append("unexpected_done_count")
    result["all_bytes_accepted_locally"] = result["sent_bytes"] == len(data)
    if "stop_to_close_ms" in result:
        result["wall_rtf"] = round((time.monotonic() - start) / result["audio_s"], 3)
    return result


def metrics(path: Path) -> dict[str, Any]:
    values: dict[str, list[float]] = {}
    for line in path.read_text().splitlines():
        found = re.search(r"stage=(\w+) duration_ms=([0-9.]+)", line)
        if found:
            values.setdefault(found.group(1), []).append(float(found.group(2)))
    return {key: summary(numbers) for key, numbers in values.items()}


def environment_info() -> dict[str, Any]:
    """Record revisions and versions without saving local changes or secrets."""
    revision: str | None
    dirty: bool | None
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
        dirty = bool(
            subprocess.check_output(
                ["git", "status", "--porcelain"], text=True, stderr=subprocess.DEVNULL
            ).strip()
        )
    except (OSError, subprocess.CalledProcessError):
        revision, dirty = None, None
    versions: dict[str, str | None] = {}
    for name in ("faster-whisper", "ctranslate2", "onnxruntime", "websockets", "uvicorn"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = None
    return {"git_commit": revision, "working_tree_dirty": dirty, "versions": versions}


async def benchmark(
    model: Path,
    output: Path,
    timeout_s: float,
    *,
    runs: int = 5,
    frame_ms: int = 100,
    silence_ms: int = 1000,
    mode: str = "live",
    partials: bool = False,
    length: str = "medium",
) -> None:
    if not model.is_absolute() or not (model / "model.bin").is_file():
        raise ValueError("--model-dir must be an absolute path to a cached local model")
    with tempfile.TemporaryDirectory(prefix="audiolens-bench-") as tmp:
        directory = Path(tmp)
        samples = synthesize(directory, length)
        with socket.socket() as listener, (directory / "server.log").open("w+") as log:
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
            port = listener.getsockname()[1]
            before_cpu = resource.getrusage(resource.RUSAGE_CHILDREN)
            cpu_start = before_cpu.ru_utime + before_cpu.ru_stime
            child = subprocess.Popen(  # noqa: ASYNC220 -- short-lived isolated benchmark process
                [sys.executable, __file__, "--worker", str(model), str(listener.fileno())],
                pass_fds=(listener.fileno(),),
                stdout=log,
                stderr=log,
                env={**os.environ, "HF_HUB_OFFLINE": "1"},
            )
            try:
                import urllib.error
                import urllib.request

                deadline = time.monotonic() + timeout_s
                while time.monotonic() < deadline:
                    if child.poll() is not None:
                        raise RuntimeError("isolated server failed to start")
                    try:
                        with urllib.request.urlopen(  # noqa: ASYNC210 -- isolated startup probe
                            f"http://127.0.0.1:{port}/health", timeout=1
                        ):
                            break
                    except (OSError, urllib.error.URLError):
                        await asyncio.sleep(0.2)
                else:
                    raise TimeoutError("isolated server startup timed out")
                results = [
                    await stream(
                        f"ws://127.0.0.1:{port}/ws",
                        samples[index % len(samples)],
                        timeout_s,
                        frame_ms=frame_ms,
                        silence_ms=silence_ms,
                        mode=mode,
                        partials=partials,
                    )
                    for index in range(runs)
                ]
            finally:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait()
            log.flush()
            after_cpu = resource.getrusage(resource.RUSAGE_CHILDREN)
            server_cpu = after_cpu.ru_utime + after_cpu.ru_stime - cpu_start
            report = {
                "method": "isolated loopback websocket, generated speech, CPU, no translator",
                "environment": await asyncio.to_thread(environment_info),
                "model": model.parent.parent.name,
                "snapshot": model.name,
                "cpu_threads": 1,
                "server_process_cpu_seconds": round(server_cpu, 3),
                "settings": {
                    "compute_type": "int8",
                    "beam_size": 5,
                    "source_lang": "en",
                    "target_lang": "fr",
                    "translation": False,
                    "partials": partials,
                    "mode": mode,
                    "phrase_length": length,
                    "silence_ms": silence_ms,
                    "frame_ms": frame_ms,
                    "vad_threshold": 0.5,
                    "vad_min_silence_ms": 500,
                    "vad_max_segment_s": 5,
                },
                "runs": results,
                "metrics_ms": metrics(directory / "server.log"),
                "stop_to_close_ms": summary(
                    [r["stop_to_close_ms"] for r in results if "stop_to_close_ms" in r]
                ),
                "wall_rtf": summary([r["wall_rtf"] for r in results if "wall_rtf" in r]),
                "estimated_final_lag_ms": summary(
                    [f["arrival_minus_segment_end_ms"] for r in results for f in r["finals"]]
                ),
                "timeouts": sum(r["timeout"] for r in results),
                "errors": sum(
                    bool(r["errors"]) or r.get("close_code") != 1000 or not r["finals"]
                    for r in results
                ),
                "server_audio_receipt_acknowledgement": False,
            }
            await asyncio.to_thread(
                output.write_text, json.dumps(report, ensure_ascii=False, indent=2) + "\n"
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-dir", type=Path)
    parser.add_argument("--output", type=Path, default=Path("/tmp/audiolens-real-ws.json"))
    parser.add_argument("--timeout", type=float, default=60)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--frame-ms", type=int, choices=[20, 50, 100], default=100)
    parser.add_argument("--silence-ms", type=int, default=1000)
    parser.add_argument("--length", choices=["short", "medium", "long"], default="medium")
    parser.add_argument("--mode", choices=["live", "clip"], default="live")
    parser.add_argument("--partials", action="store_true")
    parser.add_argument("--worker", nargs=2, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker[0], int(args.worker[1]))
        return
    if not args.model_dir or args.timeout <= 0 or args.runs < 1 or args.silence_ms < 0:
        parser.error("--model-dir and a positive --timeout are required")
    asyncio.run(
        benchmark(
            args.model_dir,
            args.output,
            args.timeout,
            runs=args.runs,
            frame_ms=args.frame_ms,
            silence_ms=args.silence_ms,
            mode=args.mode,
            partials=args.partials,
            length=args.length,
        )
    )


if __name__ == "__main__":
    main()
