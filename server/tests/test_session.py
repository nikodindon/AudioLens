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

    async def ensure_model(self, requested_model: str | None = None) -> ModelSpec:
        return self.spec

    async def transcribe(self, audio, **kwargs) -> Transcription:
        self.calls.append({"samples": audio.size, **kwargs})
        return Transcription(
            segments=[Segment(start_s=0.0, end_s=audio.size / 16000, text=self.text)],
            language="ja",
            language_probability=0.97,
            duration_s=audio.size / 16000,
            inference_s=0.1,
            spec=self.spec,
        )

    async def load_model(self, *args, **kwargs):
        return self.spec


def make_spec() -> ModelSpec:
    return ModelSpec(model="small", device="cpu", compute_type="int8")


async def _force_vad(session: Session, monkeypatch) -> None:
    async def fake_vad(audio):
        return np.ones(max(1, audio.size // 512), dtype=np.float32)

    async def fake_ensure():
        return None

    monkeypatch.setattr(session, "_vad_probs", fake_vad)
    monkeypatch.setattr(session, "_ensure_vad", fake_ensure)


async def test_finalize_on_stop(config: Config, monkeypatch) -> None:
    engine = FakeEngine(make_spec())
    session = Session(config, engine)  # type: ignore[arg-type]
    await _force_vad(session, monkeypatch)
    session.accept_audio(np.zeros(16000, dtype=np.float32).astype("<i2").tobytes())

    finals = await session.finalize()
    assert len(finals) == 1
    assert finals[0].src_lang == "ja"


async def test_clip_buffers_past_segment_limit_until_stop(config: Config, monkeypatch) -> None:
    engine = FakeEngine(make_spec())
    session = Session(config, engine)  # type: ignore[arg-type]
    session.set_live_config(mode="clip", partials=True)
    await _force_vad(session, monkeypatch)
    session.accept_audio(np.zeros(int((config.vad.max_segment_s + 1) * 16000), dtype="<i2").tobytes())

    assert await session.process() == []
    assert engine.calls == []
    result = await session.finalize()
    assert [item.type for item in result] == ["final", "done"]
    assert len(engine.calls) == 1
    assert await session.finalize() == []


async def test_finalize_without_audio_is_noop(config: Config, monkeypatch) -> None:
    engine = FakeEngine(make_spec())
    session = Session(config, engine)  # type: ignore[arg-type]
    await _force_vad(session, monkeypatch)
    assert await session.finalize() == []
    assert await session.finalize() == []


async def test_target_equal_source_translates_passthrough(config: Config, monkeypatch) -> None:
    engine = FakeEngine(make_spec())
    session = Session(config, engine)  # type: ignore[arg-type]
    await _force_vad(session, monkeypatch)
    session.set_live_config(target_lang="ja", translate=True)
    session.accept_audio(np.zeros(16000, dtype=np.float32).astype("<i2").tobytes())
    finals = await session.finalize()
    assert finals[0].text_tgt == finals[0].text_src


async def test_finalize_is_idempotent(config: Config, monkeypatch) -> None:
    engine = FakeEngine(make_spec())
    session = Session(config, engine)  # type: ignore[arg-type]
    await _force_vad(session, monkeypatch)
    session.accept_audio(np.zeros(16000, dtype=np.float32).astype("<i2").tobytes())
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
        session.accept_audio(np.zeros(16000 * 10 + 1, dtype="<i2").tobytes())
    assert excinfo.value.code == "audio_too_long"


def test_audio_after_finalize_is_rejected(config: Config) -> None:
    session = Session(config, FakeEngine(make_spec()))  # type: ignore[arg-type]
    session._finalized = True
    with pytest.raises(AudioError) as excinfo:
        session.accept_audio(b"\x00\x00")
    assert excinfo.value.code == "session_closed"
