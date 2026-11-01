from __future__ import annotations

import numpy as np
import pytest
from app import stt
from app.config import Config
from app.stt import ModelLoadError, WhisperEngine, resolve_spec


def test_requested_allowed_model_is_accepted(config_factory) -> None:
    config = config_factory()
    spec = resolve_spec(config.whisper, "tiny")
    assert spec.model == "tiny"
    assert spec.model_accepted is True
    assert spec.device == "cpu"


def test_requested_disallowed_model_falls_back_to_config(config_factory) -> None:
    config = config_factory()
    spec = resolve_spec(config.whisper, "large-v3")
    assert spec.model == "small"
    assert spec.model_accepted is False


def test_no_requested_model_uses_config(config_factory) -> None:
    config = config_factory()
    spec = resolve_spec(config.whisper, None)
    assert spec.model == "small"
    assert spec.model_accepted is False


def test_cuda_falls_back_to_cpu_when_unavailable(config_factory, monkeypatch) -> None:
    config = config_factory(whisper={"device": "cuda", "compute_type": "auto"})
    monkeypatch.setattr(stt, "_cuda_available", lambda: (False, "no device"))
    monkeypatch.setattr(stt, "supported_compute_types", lambda device: {"int8", "float32"})
    spec = resolve_spec(config.whisper)
    assert spec.device == "cpu"
    assert spec.compute_type == "int8"
    assert "no device" in (spec.fallback_reason or "")


def test_cuda_without_fallback_raises(config_factory, monkeypatch) -> None:
    config = config_factory(whisper={"device": "cuda", "allow_cpu_fallback": False})
    monkeypatch.setattr(stt, "_cuda_available", lambda: (False, "no device"))
    with pytest.raises(ModelLoadError, match="CUDA requested"):
        resolve_spec(config.whisper)


def test_auto_prefers_int8_on_pascal(config_factory, monkeypatch) -> None:
    config = config_factory(whisper={"device": "auto", "compute_type": "auto"})
    monkeypatch.setattr(stt, "_cuda_available", lambda: (True, "ok"))
    monkeypatch.setattr(
        stt, "supported_compute_types", lambda device: {"float32", "int8", "int8_float32"}
    )
    spec = resolve_spec(config.whisper)
    assert spec.device == "cuda"
    assert spec.compute_type == "int8"


def test_auto_prefers_int8_float16_on_modern_gpu(config_factory, monkeypatch) -> None:
    config = config_factory(whisper={"device": "auto", "compute_type": "auto"})
    monkeypatch.setattr(stt, "_cuda_available", lambda: (True, "ok"))
    monkeypatch.setattr(
        stt,
        "supported_compute_types",
        lambda device: {"float32", "int8_float32", "int8_float16", "float16"},
    )
    spec = resolve_spec(config.whisper)
    assert spec.compute_type == "int8_float16"


def test_unsupported_explicit_compute_type_falls_back(config_factory, monkeypatch) -> None:
    config = config_factory(whisper={"device": "auto", "compute_type": "float16"})
    monkeypatch.setattr(stt, "_cuda_available", lambda: (True, "ok"))
    monkeypatch.setattr(stt, "supported_compute_types", lambda device: {"float32", "int8_float32"})
    spec = resolve_spec(config.whisper)
    assert spec.compute_type == "int8_float32"
    assert "unsupported" in (spec.fallback_reason or "")


class _FakeModel:
    def __init__(self, spec) -> None:
        self.spec = spec
        self.calls: list[dict] = []

    def transcribe(self, audio, **kwargs):
        self.calls.append(kwargs)

        class Segment:
            start = 0.5
            end = 2.5
            text = " hello"

        class Info:
            language = "en"
            language_probability = 0.93

        return [Segment()], Info()


@pytest.mark.asyncio
async def test_engine_serialises_inference_and_reports_spec(config_factory, monkeypatch) -> None:
    config = config_factory(whisper={"compute_type": "int8", "beam_size": 5})
    created: list[_FakeModel] = []

    def fake_construct(self, spec):
        model = _FakeModel(spec)
        created.append(model)
        return model

    monkeypatch.setattr(WhisperEngine, "_construct", fake_construct)
    engine = WhisperEngine(config)
    audio = np.zeros(16000, dtype=np.float32)
    first = await engine.transcribe(audio)
    second = await engine.transcribe(audio)

    assert first.language == "en"
    assert first.language_probability == pytest.approx(0.93)
    assert first.duration_s == pytest.approx(1.0)
    assert first.spec.model == "small"
    assert len(created) == 1  # model reused
    assert created[0].calls[0]["beam_size"] == 5
    assert created[0].calls[0]["condition_on_previous_text"] is False
    assert second.segments[0].text == " hello"


@pytest.mark.asyncio
async def test_engine_evicts_old_models(config_factory, monkeypatch) -> None:
    config = config_factory(whisper={"allowed_models": ["tiny", "base"], "max_loaded_models": 1})

    def fake_construct(self, spec):
        return _FakeModel(spec)

    monkeypatch.setattr(WhisperEngine, "_construct", fake_construct)
    engine = WhisperEngine(config)
    await engine.ensure_model("tiny")
    await engine.ensure_model("base")
    assert engine.loaded_models() == ["base"]


def test_config_accepts_no_whitelist_entries_beyond_default(config_factory) -> None:
    config: Config = config_factory()
    assert config.whisper.allowed_models == ["tiny", "base", "small"]


async def test_lazy_segments_run_off_event_loop(config_factory, monkeypatch) -> None:
    import asyncio
    import threading

    caller = threading.get_ident()
    threads = []

    class LazyModel(_FakeModel):
        def transcribe(self, audio, **kwargs):
            segments, info = super().transcribe(audio, **kwargs)

            def generate():
                threads.append(threading.get_ident())
                yield from segments

            return generate(), info

    monkeypatch.setattr(WhisperEngine, "_construct", lambda self, spec: LazyModel(spec))
    engine = WhisperEngine(config_factory())
    result = await engine.transcribe(np.zeros(16000, dtype=np.float32))
    assert result.text == "hello"
    assert threads and threads[0] != caller
    await asyncio.sleep(0)


async def test_cancel_keeps_inference_serialized_until_worker_finishes(config_factory, monkeypatch):
    import asyncio
    import threading

    entered, unblock = threading.Event(), threading.Event()
    invocations = []

    class BlockingModel(_FakeModel):
        def transcribe(self, audio, **kwargs):
            segments, info = super().transcribe(audio, **kwargs)

            def generate():
                invocations.append(1)
                entered.set()
                if not unblock.wait(3):
                    raise TimeoutError("test worker did not finish")
                yield from segments

            return generate(), info

    monkeypatch.setattr(WhisperEngine, "_construct", lambda self, spec: BlockingModel(spec))
    engine = WhisperEngine(config_factory())
    audio = np.zeros(16000, dtype=np.float32)
    first = asyncio.create_task(engine.transcribe(audio))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = asyncio.create_task(engine.transcribe(audio))
        await asyncio.sleep(0.05)
        assert len(invocations) == 1
        assert not second.done()
    finally:
        unblock.set()
    result = await asyncio.wait_for(second, 3)
    assert result.text == "hello"
    assert len(invocations) == 2


async def test_generator_failure_releases_semaphore(config_factory, monkeypatch):
    import asyncio

    class FailingOnce(_FakeModel):
        failures = 0

        def transcribe(self, audio, **kwargs):
            segments, info = super().transcribe(audio, **kwargs)

            def generate():
                self.failures += 1
                if self.failures == 1:
                    raise ValueError("synthetic failure")
                yield from segments

            return generate(), info

    monkeypatch.setattr(WhisperEngine, "_construct", lambda self, spec: FailingOnce(spec))
    engine = WhisperEngine(config_factory())
    audio = np.zeros(16000, dtype=np.float32)
    with pytest.raises(ValueError, match="synthetic failure"):
        await engine.transcribe(audio)
    assert (await asyncio.wait_for(engine.transcribe(audio), 2)).text == "hello"
