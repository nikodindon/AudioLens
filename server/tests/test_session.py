from __future__ import annotations

import numpy as np
import pytest
from app.config import Config
from app.session import AudioError, Session
from app.stt import ModelSpec, Segment, Transcription


class FakeEngine:
    def __init__(self, spec: ModelSpec, text: str = " hello") -> None:
        self.spec = spec
        self.text = text
        self.calls: list[dict] = []
        self._requested: str | None = None

    def loaded_models(self) -> list[str]:
        return [self.spec.model]

    async def ensure_model(self, requested_model: str | None = None) -> ModelSpec:
        self._requested = requested_model
        return self.spec

    async def transcribe(self, audio, **kwargs) -> Transcription:
        self.calls.append({"samples": audio.size, **kwargs})
        segments = [Segment(start_s=0.25, end_s=2.0, text=self.text)] if audio.size else []
        return Transcription(
            segments=segments,
            language="ja",
            language_probability=0.97,
            duration_s=audio.size / 16000,
            inference_s=0.1,
            spec=self.spec,
        )


def make_spec() -> ModelSpec:
    return ModelSpec(model="small", device="cpu", compute_type="int8")


async def test_finalize_emits_ordered_finals(config: Config) -> None:
    engine = FakeEngine(make_spec())
    session = Session(config, engine)  # type: ignore[arg-type]
    session.accept_audio(b"\x00\x01" * 16000)  # 1 s

    finals = await session.finalize()

    assert len(finals) == 1
    assert finals[0].segment_id == 0
    assert finals[0].start_ms == 250
    assert finals[0].end_ms == 2000
    assert finals[0].src_lang == "ja"
    assert finals[0].text_src == "hello"
    assert finals[0].text_tgt is None  # translation lands in Phase 3
    assert engine.calls[0]["samples"] == 16000


async def test_finalize_without_audio_is_noop(config: Config) -> None:
    engine = FakeEngine(make_spec())
    session = Session(config, engine)  # type: ignore[arg-type]
    assert await session.finalize() == []
    assert await session.finalize() == []  # idempotent
    assert engine.calls == []


async def test_target_equal_source_translates_passthrough(config: Config) -> None:
    engine = FakeEngine(make_spec())
    session = Session(config, engine)  # type: ignore[arg-type]
    session.set_live_config(target_lang="ja", translate=True)
    session.accept_audio(b"\x00\x00" * 3200)
    finals = await session.finalize()
    assert finals[0].text_tgt == finals[0].text_src


async def test_finalize_is_idempotent(config: Config) -> None:
    engine = FakeEngine(make_spec())
    session = Session(config, engine)  # type: ignore[arg-type]
    session.accept_audio(b"\x00\x00" * 3200)
    first = await session.finalize()
    second = await session.finalize()
    assert len(first) == 1
    assert second == []


def test_odd_frame_length_is_rejected(config: Config) -> None:
    session = Session(config, FakeEngine(make_spec()))  # type: ignore[arg-type]
    with pytest.raises(AudioError, match="even byte length") as excinfo:
        session.accept_audio(b"\x00")
    assert excinfo.value.code == "bad_audio_frame"


def test_audio_limit_is_enforced(config_factory) -> None:
    config = config_factory(server={"max_audio_seconds": 10})
    session = Session(config, FakeEngine(make_spec()))  # type: ignore[arg-type]
    with pytest.raises(AudioError) as excinfo:
        session.accept_audio(b"\x00\x00" * (16000 * 10 + 1))
    assert excinfo.value.code == "audio_too_long"


def test_audio_after_finalize_is_rejected(config: Config) -> None:
    session = Session(config, FakeEngine(make_spec()))  # type: ignore[arg-type]
    session.finalized = True
    with pytest.raises(AudioError) as excinfo:
        session.accept_audio(b"\x00\x00")
    assert excinfo.value.code == "session_closed"


def test_decoding_converts_pcm_to_float(config: Config) -> None:
    session = Session(config, FakeEngine(make_spec()))  # type: ignore[arg-type]
    session.accept_audio(np.array([0, 16384, -32768], dtype="<i2").tobytes())
    decoded = session._decode()
    assert decoded.dtype == np.float32
    assert decoded[1] == pytest.approx(0.5)
    assert decoded[2] == pytest.approx(-1.0)
