"""One audio streaming session (Phase 1: buffered batch transcription).

Phase 1 keeps the audio of a session in memory and transcribes it when the
client sends ``stop``. VAD-based segmentation and partials arrive in Phase 2;
the public surface (``accept_audio`` / ``finalize``) is designed to stay.
"""

from __future__ import annotations

import logging
import time
import uuid

import numpy as np

from .config import Config
from .protocol import FinalMessage
from .stt import SAMPLE_RATE, WhisperEngine

logger = logging.getLogger(__name__)

BYTES_PER_SAMPLE = 2


class AudioError(ValueError):
    """Invalid or unacceptable audio frame."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Session:
    def __init__(
        self, config: Config, engine: WhisperEngine, session_id: str | None = None
    ) -> None:
        self.id = session_id or uuid.uuid4().hex[:12]
        self.config = config
        self.engine = engine
        self.source_lang = "auto"
        self.target_lang = config.translation.target_lang
        self.translate = True
        self.partials = True
        self._buffer = bytearray()
        self._max_bytes = config.server.max_audio_seconds * SAMPLE_RATE * BYTES_PER_SAMPLE
        self.created_at = time.monotonic()
        self.finalized = False
        self._pending_audio = np.zeros(0, dtype=np.float32)
        self._next_segment_id = 0

    # -- audio ------------------------------------------------------------- #

    @property
    def audio_seconds(self) -> float:
        return len(self._buffer) / (SAMPLE_RATE * BYTES_PER_SAMPLE)

    @property
    def bytes_received(self) -> int:
        return len(self._buffer)

    def accept_audio(self, frame: bytes) -> None:
        if self.finalized:
            raise AudioError("session_closed", "the session is already finished")
        if not frame:
            return
        if len(frame) % BYTES_PER_SAMPLE != 0:
            raise AudioError(
                "bad_audio_frame",
                f"PCM s16le frames must have an even byte length, got {len(frame)}",
            )
        if len(self._buffer) + len(frame) > self._max_bytes:
            raise AudioError(
                "audio_too_long",
                f"session exceeds the {self.config.server.max_audio_seconds}s audio limit",
            )
        self._buffer.extend(frame)

    def _decode(self) -> np.ndarray:
        if not self._buffer:
            return np.zeros(0, dtype=np.float32)
        samples = np.frombuffer(bytes(self._buffer), dtype="<i2")
        return samples.astype(np.float32) / 32768.0

    # -- transcription ----------------------------------------------------- #

    async def finalize(self) -> list[FinalMessage]:
        """Transcribe buffered audio and return final messages (idempotent)."""
        if self.finalized:
            return []
        self.finalized = True

        audio = self._decode()
        if audio.size == 0:
            logger.info("session %s stopped without audio", self.id)
            return []

        language = None if self.source_lang == "auto" else self.source_lang
        try:
            result = await self.engine.transcribe(
                audio,
                language=language,
                task="transcribe",
            )
        except Exception:
            logger.exception("transcription failed for session %s", self.id)
            raise

        logger.info(
            "session %s transcribed audio=%.1fs inference=%.2fs rtf=%.2f "
            "lang=%s prob=%.2f segments=%d",
            self.id,
            result.duration_s,
            result.inference_s,
            result.inference_s / result.duration_s if result.duration_s else 0.0,
            result.language,
            result.language_probability,
            len(result.segments),
        )

        messages: list[FinalMessage] = []
        for segment in result.segments:
            text_src = segment.text.strip()
            if not text_src:
                continue
            text_tgt = self._translate(text_src, result.language)
            start_ms = round(segment.start_s * 1000)
            end_ms = round(segment.end_s * 1000)
            messages.append(
                FinalMessage(
                    segment_id=self._next_segment_id,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    src_lang=result.language,
                    src_lang_prob=round(float(result.language_probability), 4),
                    text_src=text_src,
                    text_tgt=text_tgt,
                )
            )
            self._next_segment_id += 1
        return messages

    def _translate(self, text: str, src_lang: str) -> str | None:
        """Phase 1 has no translation engine: pass text through only when relevant."""
        if not self.translate or not text:
            return None
        if src_lang == self.target_lang:
            return text
        # Translation engines are implemented in Phase 3.
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
