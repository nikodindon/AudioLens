"""One audio streaming session with Silero VAD segmentation (Phase 2).

Phase 1 accumulated whole-session audio; this version segments speech into
individual utterances (up to ``vad.max_segment_s``), emits partials roughly once
per second and finalises each segment after ``vad.min_silence_ms`` of silence.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import numpy as np

from .config import Config
from .protocol import FinalMessage, PartialMessage
from .stt import SAMPLE_RATE, WhisperEngine

logger = logging.getLogger(__name__)

BYTES_PER_SAMPLE = 2
FRAMES_PER_WINDOW = 512  # Silero expects 512-sample windows at 16 kHz (32 ms)
SILENCE_WINDOW_S = FRAMES_PER_WINDOW / SAMPLE_RATE


class AudioError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Session:
    def __init__(
        self,
        config: Config,
        engine: WhisperEngine,
        translator=None,
        session_id: str | None = None,
    ) -> None:
        self.id = session_id or _new_id()
        self.config = config
        self.engine = engine
        self.translator = translator

        self.source_lang = "auto"
        self.target_lang = config.translation.target_lang
        self.translate = True
        self.partials = True

        self._max_seconds = config.server.max_audio_seconds
        self._vad_model: Any = None
        self._vad_lock = asyncio.Lock()

        self._committed_seconds = 0.0
        self._current_chunks: list[np.ndarray] = []
        self._current_seconds = 0.0
        self._speech_seen = False
        self._last_partial_seconds = 0.0
        self._last_partial_text: str | None = None

        self._locked_language: str | None = None
        self._next_segment_id = 0
        self._finalized = False

        self.created_at = time.monotonic()

    # ------------------------------------------------------------------ #
    # Audio intake
    # ------------------------------------------------------------------ #
    def accept_audio(self, frame: bytes) -> None:
        if self._finalized:
            raise AudioError("session_closed", "the session is already finished")
        if not frame:
            return
        if len(frame) % BYTES_PER_SAMPLE != 0:
            raise AudioError("bad_audio_frame", f"expected even byte length, got {len(frame)}")
        samples = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768.0
        if (
            self._committed_seconds + self._current_seconds + samples.size / SAMPLE_RATE
            > self._max_seconds
        ):
            raise AudioError("audio_too_long", f"session exceeds {self._max_seconds}s audio limit")
        self._current_chunks.append(samples)
        self._current_seconds += samples.size / SAMPLE_RATE

    @property
    def audio_seconds(self) -> float:
        return self._committed_seconds + self._current_seconds

    # ------------------------------------------------------------------ #
    # VAD + segmentation
    # ------------------------------------------------------------------ #
    async def process(self) -> list[Any]:
        """Process buffered audio; may return partial/final messages."""
        messages: list[Any] = []
        if self._finalized or not self._current_chunks:
            return messages

        audio = self._concat_current()
        if audio.size == 0:
            return messages

        vad_prob = await self._vad_probs(audio)
        speech_flags = vad_prob >= self.config.vad.threshold
        silence_windows = max(1, int(self.config.vad.min_silence_ms / (SILENCE_WINDOW_S * 1000)))
        self._speech_seen = bool(speech_flags.any())

        # Trailing silence detection.
        tail_silence = 0
        for flag in reversed(speech_flags.tolist()):
            if not flag:
                tail_silence += 1
            else:
                break
        long_enough = self._current_seconds * 1000 >= self.config.vad.min_speech_ms
        finalize_now = (
            self._speech_seen
            and long_enough
            and (
                tail_silence >= silence_windows
                or self._current_seconds >= self.config.vad.max_segment_s
            )
        )

        if finalize_now:
            messages.extend(await self._finalize_current(audio, speech_flags))
            return messages

        # Partial: roughly once per second of new speech.
        speech_duration = self._current_seconds
        partial_interval = 1.0
        if self.partials and self._speech_seen and speech_duration >= 1.0:
            if (speech_duration - self._last_partial_seconds) >= partial_interval:
                partial = await self._emit_partial(audio)
                if partial is not None:
                    messages.append(partial)
                self._last_partial_seconds = speech_duration

        # Max segment hard cap regardless of silence: force finalization.
        if self._current_seconds >= self.config.vad.max_segment_s:
            messages.extend(await self._finalize_current(audio, speech_flags))

        return messages

    async def finalize(self) -> list[Any]:
        if self._finalized:
            return []
        self._finalized = True
        messages: list[Any] = []
        if self._current_chunks:
            audio = self._concat_current()
            if audio.size:
                speech_flags = np.ones((max(1, audio.size // FRAMES_PER_WINDOW),), dtype=bool)
                messages.extend(await self._finalize_current(audio, speech_flags, final_stop=True))
        self._current_chunks.clear()
        self._current_seconds = 0.0
        return messages

    async def _finalize_current(
        self, audio: np.ndarray, speech_flags: np.ndarray, final_stop: bool = False
    ) -> list[Any]:
        # Trim trailing silence: keep up to the last speech window.
        if speech_flags.size and not final_stop:
            last_speech = 0
            for idx, flag in enumerate(speech_flags.tolist()):
                if flag:
                    last_speech = idx + 1
            if 0 < last_speech < speech_flags.size:
                trimmed = audio[: last_speech * FRAMES_PER_WINDOW]
                if trimmed.size:
                    audio = trimmed

        # Ensure multiple-of-FRAMES? trim not required for transcribe.
        if audio.size == 0 or audio.size / SAMPLE_RATE * 1000 < self.config.vad.min_speech_ms:
            self._reset_current()
            return []

        language: str | None = self._locked_language if self._locked_language else self.source_lang
        if language == "auto":
            language = None

        try:
            result = await self.engine.transcribe(
                audio,
                language=language,
                task="transcribe",
                beam_size=self.config.whisper.beam_size,
                no_speech_threshold=0.6,
                compression_ratio_threshold=2.4,
                temperature=0.0,
            )
        except Exception:
            logger.exception("transcription failed for session %s", self.id)
            return []

        # lock language on first confident detection.
        if self._locked_language is None and result.language and result.language_probability >= 0.5:
            self._locked_language = result.language

        logger.info(
            "session %s segment %d: audio=%.1fs inference=%.2fs lang=%s prob=%.2f",
            self.id,
            self._next_segment_id,
            result.duration_s,
            result.inference_s,
            result.language,
            result.language_probability,
        )

        text_src = result.text.strip()
        message: list[Any] = []
        if text_src:
            text_tgt = self._translate(text_src, result.language)
            message.append(
                FinalMessage(
                    segment_id=self._next_segment_id,
                    start_ms=round((self._committed_seconds) * 1000),
                    end_ms=round((self._committed_seconds + result.duration_s) * 1000),
                    src_lang=result.language,
                    src_lang_prob=round(float(result.language_probability), 4),
                    text_src=text_src,
                    text_tgt=text_tgt,
                )
            )
            self._next_segment_id += 1

        self._committed_seconds += audio.size / SAMPLE_RATE
        self._reset_current()
        return message

    async def _emit_partial(self, audio: np.ndarray) -> PartialMessage | None:
        # Drop trailing silence windows for cleaner partials.
        trimmed = audio
        try:
            result = await self.engine.transcribe(
                trimmed,
                language=self._locked_language
                or (None if self.source_lang == "auto" else self.source_lang),
                task="transcribe",
                beam_size=1,
                temperature=0.0,
            )
        except Exception:
            logger.exception("partial transcription failed for session %s", self.id)
            return None

        text = result.text.strip()
        if not text or text == (self._last_partial_text or "").strip():
            return None
        self._last_partial_text = text
        if self._locked_language is None and result.language and result.language_probability >= 0.5:
            self._locked_language = result.language

        return PartialMessage(segment_id=self._next_segment_id, text=text, lang=result.language)

    def _translate(self, text: str, src_lang: str) -> str | None:
        if not self.translate or not text:
            return None
        if src_lang == self.target_lang:
            return text
        if self.translator is None:
            return None
        try:
            return self.translator.translate(text, src_lang, self.target_lang)
        except Exception:
            logger.exception("translation failed for session %s", self.id)
            return None

    def set_live_config(
        self,
        *,
        source_lang: str | None = None,
        target_lang: str | None = None,
        translate: bool | None = None,
        partials: bool | None = None,
    ) -> None:
        if source_lang is not None:
            self.source_lang = source_lang
        if target_lang is not None:
            self.target_lang = target_lang
        if translate is not None:
            self.translate = translate
        if partials is not None:
            self.partials = partials

    def update_config(self, config: Config) -> None:
        self.config = config

    # ------------------------------------------------------------------ #
    # VAD internals
    # ------------------------------------------------------------------ #
    async def _ensure_vad(self) -> Any:
        async with self._vad_lock:
            if self._vad_model is not None:
                return self._vad_model

            def factory() -> Any:
                from faster_whisper.vad import get_vad_model

                return get_vad_model()

            self._vad_model = await asyncio.to_thread(factory)
            return self._vad_model

    async def _vad_probs(self, audio: np.ndarray) -> np.ndarray:
        usable = (audio.size // FRAMES_PER_WINDOW) * FRAMES_PER_WINDOW
        if usable < FRAMES_PER_WINDOW:
            return np.zeros(0, dtype=np.float32)
        trimmed = audio[:usable]
        model = await self._ensure_vad()

        def run() -> np.ndarray:
            return np.asarray(model(trimmed, FRAMES_PER_WINDOW)).reshape(-1)

        return await asyncio.to_thread(run)

    def _concat_current(self) -> np.ndarray:
        if not self._current_chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(self._current_chunks).astype(np.float32)

    def _reset_current(self) -> None:
        self._current_chunks.clear()
        self._current_seconds = 0.0
        self._speech_seen = False
        self._last_partial_seconds = 0.0
        self._last_partial_text = None


def _new_id() -> str:
    import uuid

    return uuid.uuid4().hex[:12]
