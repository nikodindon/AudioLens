#!/usr/bin/env python3
"""Generate multilingual test audio with gTTS (development helper, needs network).

    pip install gTTS
    python tools/make_samples.py --out samples

Produces 16 kHz mono 16-bit WAV files used by the benchmark and by
``tools/stream_wav.py``. The generated audio is synthetic; it is not shipped in
git (see .gitignore) but the script keeps benchmarks reproducible.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

TEXTS = {
    "ja": (
        "こんにちは。今日は天気がとてもいいですね。"
        "私は東京に住んでいて、毎朝電車で会社に通っています。"
        "週末は友達と映画を見たり、公園を散歩したりします。"
        "日本語の勉強は難しいですが、少しずつ上手になっています。"
        "来月は京都へ旅行に行く予定です。紅葉がとてもきれいだと聞きました。"
        "どんな食べ物がおいしいか、ぜひ教えてください。"
    ),
    "en": (
        "Hello and welcome. Today the weather is really nice. "
        "I live in a small city and take the train to work every morning. "
        "On weekends I meet friends, watch movies, or walk in the park. "
        "Learning a new language takes time, but I improve a little every day. "
        "Next month we are planning a trip to the mountains to see the autumn leaves. "
        "Please tell me which local dishes I should try first."
    ),
    "es": (
        "Hola, buenos días. Hoy hace muy buen tiempo en la ciudad. "
        "Vivo cerca del centro y voy al trabajo en bicicleta todas las mañanas. "
        "Los fines de semana quedo con mis amigos para ver una película o caminar por el parque. "
        "Aprender un idioma nuevo requiere paciencia, pero practico un poco cada día. "
        "El mes que viene viajaremos al norte para ver las montañas. "
        "¿Puedes recomendarme algún plato típico que tenga que probar?"
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="samples", help="output directory")
    parser.add_argument("--langs", nargs="+", default=sorted(TEXTS))
    parser.add_argument("--repeat", type=int, default=2, help="repeat the text to reach ~60 s")
    args = parser.parse_args()

    try:
        from gtts import gTTS
    except ImportError as exc:  # pragma: no cover
        raise SystemExit("gTTS is required: pip install gTTS") from exc

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for lang in args.langs:
        text = (" " + TEXTS[lang] + " ") * args.repeat
        target = out / f"{lang}_sample.wav"
        with tempfile.TemporaryDirectory() as tmp:
            mp3 = Path(tmp) / f"{lang}.mp3"
            gTTS(text=text, lang=lang).save(str(mp3))
            subprocess.run(
                [
                    "ffmpeg",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(mp3),
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    "-sample_fmt",
                    "s16",
                    str(target),
                ],
                check=True,
            )
        size = target.stat().st_size
        print(f"{target}: {size / 1024:.0f} KiB")

    if shutil.which("ffprobe"):
        for lang in args.langs:
            subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=nw=1:nk=1",
                    str(out / f"{lang}_sample.wav"),
                ],
                check=False,
            )


if __name__ == "__main__":
    main()
