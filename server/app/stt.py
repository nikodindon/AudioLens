"""faster-whisper engine management: model selection, device fallback, inference.

Design notes
------------
* CTranslate2 inference is blocking: it always runs in a worker thread and is
  serialised by a semaphore so a single GPU is never oversubscribed.
* Models are loaded lazily, at most ``whisper.max_loaded_models`` at a time, to
  protect the 4 GB of VRAM of the reference machine.
* GPU problems (missing cuDNN, out-of-memory, unsupported compute type) fall
  back to CPU when ``whisper.allow_cpu_fallback`` is enabled, and the effective
  device/compute type is always reported back to clients.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel

from .config import Config, WhisperConfig

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000

# Preferred GPU compute types, best first. int8_float16 requires compute
# capability >= 7.0 (Volta+); Pascal (6.1, GTX 10xx) only supports int8_float32.
_GPU_COMPUTE_PREFERENCE = ("int8_float16", "int8", "int8_float32", "float32")
_CPU_COMPUTE_PREFERENCE = ("int8", "int8_float32", "float32")


class ModelLoadError(RuntimeError):
    """Raised when a Whisper model cannot be loaded on any device."""


@dataclass(frozen=True)
class ModelSpec:
    """Effective model/device/compute type used for inference."""

    model: str
    device: str
    compute_type: str
    requested_model: str | None = None
    requested_device: str = "auto"
    requested_compute_type: str = "auto"
    fallback_reason: str | None = None

    @property
    def model_accepted(self) -> bool:
        if self.requested_model is None:
            return False
        return self.requested_model == self.model


@dataclass
class Segment:
    start_s: float
    end_s: float
    text: str


@dataclass
class Transcription:
    segments: list[Segment]
    language: str
    language_probability: float
    duration_s: float
    inference_s: float
    spec: ModelSpec

    @property
    def text(self) -> str:
        return " ".join(segment.text.strip() for segment in self.segments if segment.text.strip())


@dataclass
class _LoadedModel:
    model: WhisperModel
    spec: ModelSpec
    loaded_at: float = field(default_factory=time.monotonic)


def _cuda_available() -> tuple[bool, str]:
    """Return (available, reason) for the CUDA backend without loading a model."""
    try:
        import ctranslate2

        device_count = ctranslate2.get_cuda_device_count()
        if device_count <= 0:
            return False, "no CUDA device reported by CTranslate2"
        supported = ctranslate2.get_supported_compute_types("cuda")
        if not supported:
            return False, "CUDA backend reports no supported compute type"
        return True, f"{device_count} CUDA device(s), compute types: {sorted(supported)}"
    except Exception as exc:
        return False, f"CUDA backend unavailable: {exc}"


def supported_compute_types(device: str) -> set[str]:
    try:
        import ctranslate2

        return set(ctranslate2.get_supported_compute_types(device))
    except Exception:
        return {"float32"}


def resolve_spec(config: WhisperConfig, requested_model: str | None = None) -> ModelSpec:
    """Resolve device/compute type and apply the ``allowed_models`` policy.

    Pure logic (no model download) so it can be unit-tested.
    """
    requested_device = config.device
    requested_compute = config.compute_type
    fallback_reason: str | None = None

    if requested_device == "cuda":
        cuda_ok, reason = _cuda_available()
        device = "cuda" if cuda_ok else "cpu"
        if not cuda_ok:
            if not config.allow_cpu_fallback:
                raise ModelLoadError(f"CUDA requested but unavailable: {reason}")
            fallback_reason = f"CUDA requested but unavailable ({reason}); using CPU"
    elif requested_device == "auto":
        cuda_ok, reason = _cuda_available()
        device = "cuda" if cuda_ok else "cpu"
        if not cuda_ok:
            logger.info("CUDA not used (%s); running on CPU", reason)
    else:
        device = "cpu"

    supported = supported_compute_types(device)
    compute = requested_compute
    if compute == "auto":
        preference = _GPU_COMPUTE_PREFERENCE if device == "cuda" else _CPU_COMPUTE_PREFERENCE
        compute = next((item for item in preference if item in supported), "float32")
    elif compute not in supported:
        preference = _GPU_COMPUTE_PREFERENCE if device == "cuda" else _CPU_COMPUTE_PREFERENCE
        replacement = next((item for item in preference if item in supported), "float32")
        note = f"compute_type {compute!r} unsupported on {device}, using {replacement!r}"
        fallback_reason = f"{fallback_reason}; {note}" if fallback_reason else note
        compute = replacement

    model = requested_model or config.model
    if requested_model is not None and requested_model != config.model:
        if requested_model not in config.allowed_models:
            logger.warning(
                "client requested model %r which is not in whisper.allowed_models; using %r",
                requested_model,
                config.model,
            )
            model = config.model

    return ModelSpec(
        model=model,
        device=device,
        compute_type=compute,
        requested_model=requested_model,
        requested_device=requested_device,
        requested_compute_type=requested_compute,
        fallback_reason=fallback_reason,
    )


class WhisperEngine:
    """Async facade around one or more loaded faster-whisper models."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._whisper = config.whisper
        self._models: OrderedDict[str, _LoadedModel] = OrderedDict()
        self._lock = asyncio.Lock()
        self._inference_semaphore = asyncio.Semaphore(1)
        self._current_spec: ModelSpec | None = None

    @property
    def config(self) -> Config:
        return self._config

    def update_config(self, config: Config) -> None:
        """Swap configuration (hot reload). Loaded models are kept until evicted."""
        self._config = config
        self._whisper = config.whisper

    @property
    def current_spec(self) -> ModelSpec | None:
        return self._current_spec

    def loaded_models(self) -> list[str]:
        return list(self._models)

    async def ensure_model(self, requested_model: str | None = None) -> ModelSpec:
        """Load (or reuse) the model for this request and return the effective spec."""
        spec = resolve_spec(self._whisper, requested_model)
        if spec.model in self._models and self._models[spec.model].spec.device == spec.device:
            self._models.move_to_end(spec.model)
            loaded = self._models[spec.model]
            # Keep the effective compute type of the already-loaded instance.
            effective = ModelSpec(
                model=loaded.spec.model,
                device=loaded.spec.device,
                compute_type=loaded.spec.compute_type,
                requested_model=spec.requested_model,
                requested_device=spec.requested_device,
                requested_compute_type=spec.requested_compute_type,
                fallback_reason=loaded.spec.fallback_reason,
            )
            self._current_spec = effective
            return effective

        async with self._lock:
            if spec.model in self._models:
                loaded = self._models[spec.model]
                self._models.move_to_end(spec.model)
                self._current_spec = loaded.spec
                return loaded.spec

            loaded = await self._load(spec)
            self._models[spec.model] = loaded
            self._models.move_to_end(spec.model)
            while len(self._models) > self._whisper.max_loaded_models:
                evicted, _ = self._models.popitem(last=False)
                logger.info("evicted model %r from memory (max_loaded_models)", evicted)
            self._current_spec = loaded.spec
            return loaded.spec

    async def _load(self, spec: ModelSpec) -> _LoadedModel:
        logger.info(
            "loading Whisper model=%s device=%s compute_type=%s",
            spec.model,
            spec.device,
            spec.compute_type,
        )
        start = time.monotonic()
        try:
            model = await asyncio.to_thread(self._construct, spec)
        except Exception as exc:
            if spec.device == "cuda" and self._whisper.allow_cpu_fallback:
                logger.error("GPU load failed (%s); falling back to CPU", exc)
                fallback_reason = f"GPU load failed ({exc}); using CPU"
                cpu_compute = _CPU_COMPUTE_PREFERENCE[0]
                cpu_spec = ModelSpec(
                    model=spec.model,
                    device="cpu",
                    compute_type=cpu_compute,
                    requested_model=spec.requested_model,
                    requested_device=spec.requested_device,
                    requested_compute_type=spec.requested_compute_type,
                    fallback_reason=fallback_reason,
                )
                model = await asyncio.to_thread(self._construct, cpu_spec)
                spec = cpu_spec
            else:
                raise ModelLoadError(
                    f"could not load model {spec.model!r} on {spec.device}: {exc}"
                ) from exc
        elapsed = time.monotonic() - start
        logger.info(
            "model ready model=%s device=%s compute_type=%s load_time=%.2fs",
            spec.model,
            spec.device,
            spec.compute_type,
            elapsed,
        )
        return _LoadedModel(model=model, spec=spec)

    def _construct(self, spec: ModelSpec) -> WhisperModel:
        kwargs: dict[str, object] = {
            "device": spec.device,
            "compute_type": spec.compute_type,
            "cpu_threads": self._whisper.cpu_threads,
            "num_workers": 1,
        }
        if self._whisper.download_root:
            Path(self._whisper.download_root).mkdir(parents=True, exist_ok=True)
            kwargs["download_root"] = self._whisper.download_root
        return WhisperModel(spec.model, **kwargs)

    async def transcribe(
        self,
        audio: np.ndarray,
        *,
        language: str | None = None,
        requested_model: str | None = None,
        task: str = "transcribe",
        beam_size: int | None = None,
        temperature: float = 0.0,
        no_speech_threshold: float = 0.6,
        compression_ratio_threshold: float = 2.4,
    ) -> Transcription:
        """Transcribe mono float32 audio at 16 kHz (blocking work off the event loop)."""
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        spec = await self.ensure_model(requested_model)
        model = self._models[spec.model].model
        beam = self._whisper.beam_size if beam_size is None else beam_size

        async with self._inference_semaphore:
            start = time.monotonic()
            segments, info = await asyncio.to_thread(
                model.transcribe,
                audio,
                language=language,
                task=task,
                beam_size=beam,
                temperature=temperature,
                no_speech_threshold=no_speech_threshold,
                compression_ratio_threshold=compression_ratio_threshold,
                condition_on_previous_text=False,
                vad_filter=False,
                word_timestamps=False,
            )
            collected = [
                Segment(start_s=segment.start, end_s=segment.end, text=segment.text)
                for segment in segments
            ]
            inference_s = time.monotonic() - start

        return Transcription(
            segments=collected,
            language=info.language,
            language_probability=info.language_probability,
            duration_s=len(audio) / SAMPLE_RATE,
            inference_s=inference_s,
            spec=spec,
        )

    async def reload(self) -> ModelSpec:
        """Drop every loaded model and load the configured default again."""
        async with self._lock:
            self._models.clear()
            self._current_spec = None
        return await self.ensure_model(None)
