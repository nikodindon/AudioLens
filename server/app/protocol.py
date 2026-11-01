"""Pydantic models for the client <-> server WebSocket protocol (protocol v1).

See docs/protocol.md for the human-readable contract.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

PROTOCOL_VERSION = 1
SERVER_VERSION = "0.1.0"

SUPPORTED_SAMPLE_RATE: Literal[16000] = 16000
SUPPORTED_CHANNELS: Literal[1] = 1
SUPPORTED_FORMAT: Literal["pcm_s16le"] = "pcm_s16le"


class AudioFormat(BaseModel):
    model_config = ConfigDict(extra="ignore")

    format: Literal["pcm_s16le"] = SUPPORTED_FORMAT
    sample_rate: Literal[16000] = SUPPORTED_SAMPLE_RATE
    channels: Literal[1] = SUPPORTED_CHANNELS


class StartMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["start"]
    protocol: int = PROTOCOL_VERSION
    audio: AudioFormat = Field(default_factory=AudioFormat)
    source_lang: str = "auto"
    target_lang: str = "fr"
    # A *request*, not a guarantee: the server only honours models listed in
    # whisper.allowed_models, otherwise it uses the configured default model.
    whisper_model: str | None = None
    translate: bool = True
    partials: bool = True

    @property
    def audio_format_supported(self) -> bool:
        return True  # enforced by the field types above


class ConfigMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["config"]
    source_lang: str | None = None
    target_lang: str | None = None
    translate: bool | None = None
    partials: bool | None = None


class StopMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["stop"]


class PingMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: Literal["ping"]


ClientMessage = Annotated[
    StartMessage | ConfigMessage | StopMessage | PingMessage,
    Field(discriminator="type"),
]

_client_adapter: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


class ClientMessageError(ValueError):
    """Raised when an incoming text frame is not a valid client message."""


def parse_client_message(raw: str) -> ClientMessage:
    try:
        return _client_adapter.validate_json(raw)
    except ValidationError as exc:
        raise ClientMessageError(_format_error(exc)) from exc


def _format_error(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors()[:3]:
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        parts.append(f"{location}: {error['msg']}")
    return "; ".join(parts)


# --------------------------------------------------------------------------- #
# Server -> client messages
# --------------------------------------------------------------------------- #


class ReadyMessage(BaseModel):
    type: Literal["ready"] = "ready"
    session_id: str
    server_version: str = SERVER_VERSION
    protocol: int = PROTOCOL_VERSION
    whisper_model: str
    whisper_model_requested: str | None = None
    whisper_model_accepted: bool
    whisper_device: str
    whisper_compute_type: str
    translation_engine: str
    translate: bool


class PartialMessage(BaseModel):
    type: Literal["partial"] = "partial"
    segment_id: int
    text: str
    lang: str


class FinalMessage(BaseModel):
    type: Literal["final"] = "final"
    segment_id: int
    start_ms: int
    end_ms: int
    src_lang: str
    src_lang_prob: float
    text_src: str
    text_tgt: str | None = None


class ErrorMessage(BaseModel):
    type: Literal["error"] = "error"
    code: str
    message: str


class PongMessage(BaseModel):
    type: Literal["pong"] = "pong"


ServerMessage = ReadyMessage | PartialMessage | FinalMessage | ErrorMessage | PongMessage
