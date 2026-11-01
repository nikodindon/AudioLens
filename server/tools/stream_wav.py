#!/usr/bin/env python3
"""Stream a WAV file to an AudioLens server as if it were live microphone audio.

Example:
    python tools/stream_wav.py ../samples/ja.wav \
        --url ws://localhost:8765/ws --token "$AUDIOLENS_TOKEN" --target fr

The WAV is resampled to 16 kHz mono s16le. By default it is sent in real time
(``--speed 1.0``); use a higher speed to stress the server.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np

try:  # websockets >= 13
    from websockets.asyncio.client import connect
except ImportError:  # pragma: no cover - older websockets
    from websockets.client import connect  # type: ignore[attr-defined,no-redef]

SAMPLE_RATE = 16000


def load_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    if width != 2:
        raise SystemExit(f"{path}: expected 16-bit PCM WAV, got {width * 8}-bit")
    samples = np.frombuffer(frames, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1)
    if rate != SAMPLE_RATE:
        duration = samples.size / rate
        target = round(duration * SAMPLE_RATE)
        source_t = np.arange(samples.size) / rate
        target_t = np.arange(target) / SAMPLE_RATE
        samples = np.interp(target_t, source_t, samples).astype(np.float32)
    return samples


def to_pcm16(samples: np.ndarray) -> bytes:
    clipped = np.clip(samples, -1.0, 1.0)
    return (clipped * 32767.0).astype("<i2").tobytes()


async def run(args: argparse.Namespace) -> int:
    url = args.url
    headers = {"Authorization": f"Bearer {args.token}"}

    audio = load_wav(Path(args.wav))
    if audio.size == 0:
        raise SystemExit("empty WAV file")
    duration = audio.size / SAMPLE_RATE
    pcm = to_pcm16(audio)
    chunk_bytes = max(320, int(SAMPLE_RATE * args.chunk_ms / 1000) * 2)
    chunks = [pcm[i : i + chunk_bytes] for i in range(0, len(pcm), chunk_bytes)]

    print(f"audio: {args.wav} ({duration:.1f}s, {len(chunks)} frames of {args.chunk_ms}ms)")
    start_message = {
        "type": "start",
        "protocol": 1,
        "audio": {"format": "pcm_s16le", "sample_rate": SAMPLE_RATE, "channels": 1},
        "source_lang": args.source,
        "target_lang": args.target,
        "whisper_model": args.model,
        "translate": not args.no_translate,
        "partials": True,
    }

    finals: list[dict[str, Any]] = []
    started = time.monotonic()
    send_deadline = started
    async with connect(
        url, max_size=None, open_timeout=args.timeout, additional_headers=headers
    ) as ws:
        await ws.send(json.dumps(start_message))
        ready_raw = await ws.recv()
        if isinstance(ready_raw, bytes):
            ready_raw = ready_raw.decode()
        ready = json.loads(ready_raw)
        print(f"ready: {ready_raw}")
        if ready.get("type") == "error":
            return 2
        if ready.get("type") == "ready" and not ready.get("whisper_model_accepted", True):
            print(
                f"note: server did not accept requested model {args.model!r}; "
                f"using {ready.get('whisper_model')!r}",
                file=sys.stderr,
            )

        for index, chunk in enumerate(chunks, start=1):
            await ws.send(chunk)
            send_deadline += (args.chunk_ms / 1000.0) / args.speed
            delay = send_deadline - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            if args.progress and index % max(1, int(1000 / args.chunk_ms)) == 0:
                print(f"  sent {index}/{len(chunks)} frames", end="\r", file=sys.stderr)

        if args.progress:
            print("", file=sys.stderr)
        stop_sent = time.monotonic()
        await ws.send(json.dumps({"type": "stop"}))

        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=args.timeout)
            except TimeoutError:
                print("timeout waiting for server messages", file=sys.stderr)
                break
            except Exception:
                break
            if isinstance(raw, bytes):
                continue
            message = json.loads(raw)
            kind = message.get("type")
            if kind == "final":
                finals.append(message)
                total_latency = time.monotonic() - started
                print(
                    f"final #{message['segment_id']} "
                    f"[{message['start_ms'] / 1000:.1f}-{message['end_ms'] / 1000:.1f}s] "
                    f"({message['src_lang']} {message['src_lang_prob']:.2f}) "
                    f"{message['text_src']!r}"
                    + (f" -> {message['text_tgt']!r}" if message.get("text_tgt") else "")
                )
                print(
                    f"  audio clock {message['end_ms'] / 1000:.1f}s, "
                    f"wall clock {total_latency:.1f}s",
                    file=sys.stderr,
                )
            elif kind == "error":
                print(f"server error: {message}", file=sys.stderr)
            else:
                print(f"server: {raw}")

    total = time.monotonic() - started
    finalize_latency = time.monotonic() - stop_sent
    print(
        f"\nsummary: {len(finals)} final segment(s), audio {duration:.1f}s, "
        f"wall {total:.1f}s, stop->close {finalize_latency:.2f}s"
    )
    if args.json:
        print(
            json.dumps({"finals": finals, "wall_s": round(total, 3)}, ensure_ascii=False, indent=2)
        )
    return 0 if finals else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", help="path to a 16-bit WAV file")
    parser.add_argument(
        "--url",
        default="ws://localhost:8765/ws",
        help="WebSocket URL, without token (default: %(default)s)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="server token (default: $AUDIOLENS_TOKEN or $AUDIOLENS_SERVER__TOKEN)",
    )
    parser.add_argument("--source", default="auto", help="source language or 'auto'")
    parser.add_argument("--target", default="fr", help="target language")
    parser.add_argument("--model", default=None, help="requested Whisper model")
    parser.add_argument("--chunk-ms", type=int, default=100, help="frame size in ms")
    parser.add_argument(
        "--speed", type=float, default=1.0, help="1.0 = real time, 0 = as fast as possible"
    )
    parser.add_argument("--no-translate", action="store_true")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--progress", action="store_true")
    parser.add_argument("--json", action="store_true", help="dump finals as JSON")
    args = parser.parse_args()

    if args.speed <= 0:
        args.speed = 1_000_000.0
    if not args.token:
        import os

        args.token = os.environ.get("AUDIOLENS_TOKEN") or os.environ.get("AUDIOLENS_SERVER__TOKEN")
    if not args.token:
        parser.error("--token or AUDIOLENS_TOKEN is required")

    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
