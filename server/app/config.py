"""Configuration loading and validation for the AudioLens server.

Priority (lowest to highest): built-in defaults, ``config.yaml``, environment
variables (``AUDIOLENS_*`` with ``__`` nesting), explicit overrides in tests.

The YAML file is the main source of truth; environment variables are only an
override mechanism (handy for secrets and containers).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator

SERVER_DIR = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = SERVER_DIR / "config.yaml"
ENV_PREFIX = "AUDIOLENS_"
ENV_NESTING = "__"

KNOWN_COMPUTE_TYPES = {
    "auto",
    "int8",
    "int8_float32",
    "int8_float16",
    "float16",
    "float32",
    "bfloat16",
}
WEAK_TOKENS = {
    "change_me",
    "changeme",
    "secret",
    "token",
    "password",
    "CHANGE_ME_generate_with_openssl_rand_hex_16",
}


class ConfigError(ValueError):
    """Raised when the configuration cannot be loaded or is invalid."""


class ServerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Localhost by default: use `adb reverse` for dev, or the Tailscale IP on the phone.
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    token: SecretStr
    max_sessions: int = Field(default=2, ge=1, le=8)
    max_audio_seconds: int = Field(default=300, ge=10, le=7200)
    allow_query_token: bool = False
    log_level: str = "INFO"

    @field_validator("token")
    @classmethod
    def _check_token(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if len(raw) < 16:
            raise ValueError("token must be at least 16 characters (openssl rand -hex 16)")
        if raw in WEAK_TOKENS or raw.lower() in WEAK_TOKENS:
            raise ValueError(
                "token is a placeholder; generate a real one with openssl rand -hex 16"
            )
        return value

    @field_validator("log_level")
    @classmethod
    def _check_log_level(cls, value: str) -> str:
        level = value.upper()
        if level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("log_level must be one of DEBUG, INFO, WARNING, ERROR, CRITICAL")
        return level


class WhisperConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str = "small"
    device: Literal["auto", "cuda", "cpu"] = "auto"
    compute_type: str = "auto"
    beam_size: int = Field(default=5, ge=1, le=10)
    cpu_threads: int = Field(default=4, ge=1, le=64)
    download_root: str | None = None
    allowed_models: list[str] = Field(default_factory=lambda: ["tiny", "base", "small", "medium"])
    max_loaded_models: int = Field(default=1, ge=1, le=4)
    allow_cpu_fallback: bool = True

    @field_validator("model")
    @classmethod
    def _check_model(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("whisper.model must not be empty")
        return value

    @field_validator("compute_type")
    @classmethod
    def _check_compute_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in KNOWN_COMPUTE_TYPES:
            allowed = ", ".join(sorted(KNOWN_COMPUTE_TYPES))
            raise ValueError(f"whisper.compute_type must be one of: {allowed}")
        return normalized

    @field_validator("allowed_models")
    @classmethod
    def _check_allowed_models(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item and item.strip()]
        if not cleaned:
            raise ValueError("whisper.allowed_models must contain at least one model")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("whisper.allowed_models contains duplicates")
        return cleaned


class VadConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    min_silence_ms: int = Field(default=400, ge=50, le=5000)
    min_speech_ms: int = Field(default=250, ge=0, le=5000)
    max_segment_s: float = Field(default=12.0, gt=0.5, le=60.0)
    speech_pad_ms: int = Field(default=200, ge=0, le=1000)
    window_size: int = Field(default=512, ge=160, le=2048)


class TranslationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine: Literal["none", "nllb", "ollama", "argos", "openai"] = "none"
    device: Literal["auto", "cuda", "cpu"] = "cpu"
    compute_type: str = "int8"
    model: str = "facebook/nllb-200-distilled-600M"
    target_lang: str = "fr"
    ollama_url: str | None = None
    ollama_model: str | None = None
    openai_base_url: str | None = None
    openai_model: str | None = None
    openai_api_key: str | None = None

    @field_validator("compute_type")
    @classmethod
    def _check_compute_type(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in KNOWN_COMPUTE_TYPES - {"auto"}:
            allowed = ", ".join(sorted(KNOWN_COMPUTE_TYPES - {"auto"}))
            raise ValueError(f"translation.compute_type must be one of: {allowed}")
        return normalized


class SttConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine: Literal["faster-whisper"] = "faster-whisper"


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    server: ServerConfig
    whisper: WhisperConfig = Field(default_factory=WhisperConfig)
    stt: SttConfig = Field(default_factory=SttConfig)
    vad: VadConfig = Field(default_factory=VadConfig)
    translation: TranslationConfig = Field(default_factory=TranslationConfig)

    @property
    def token(self) -> str:
        return self.server.token.get_secret_value()

    def safe_dict(self) -> dict[str, Any]:
        """Config representation that is safe to log (token masked)."""
        data = self.model_dump(mode="json")
        server = dict(data["server"])
        server["token"] = "***"
        data["server"] = server
        return data


def resolve_config_path(explicit: str | os.PathLike[str] | None, env: dict[str, str]) -> Path:
    if explicit is not None:
        path = Path(explicit).expanduser()
    elif env.get(f"{ENV_PREFIX}CONFIG"):
        path = Path(env[f"{ENV_PREFIX}CONFIG"]).expanduser()
    else:
        path = DEFAULT_CONFIG_PATH
    if not path.is_file():
        raise ConfigError(
            f"configuration file not found: {path}\n"
            f"Create it with: cp config.example.yaml config.yaml "
            f"(or set {ENV_PREFIX}CONFIG)"
        )
    return path


def _parse_env_value(key: str, raw: str) -> Any:
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError as exc:  # pragma: no cover - defensive
        raise ConfigError(f"invalid value for {key}: {exc}") from exc


def env_overrides(env: dict[str, str]) -> dict[str, Any]:
    """Turn ``AUDIOLENS_WHISPER__MODEL=small`` into ``{"whisper": {"model": "small"}}``."""
    overrides: dict[str, Any] = {}
    for key, raw in env.items():
        if not key.startswith(ENV_PREFIX) or key == f"{ENV_PREFIX}CONFIG":
            continue
        suffix = key[len(ENV_PREFIX) :].lower()
        parts = suffix.split(ENV_NESTING)
        if len(parts) != 2 or not all(parts):
            raise ConfigError(
                f"unsupported environment variable {key}: expected "
                f"{ENV_PREFIX}<SECTION>{ENV_NESTING}<KEY> (e.g. {ENV_PREFIX}WHISPER__MODEL)"
            )
        section, field = parts
        overrides.setdefault(section, {})[field] = _parse_env_value(key, raw)
    return overrides


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _format_validation_error(path: Path, exc: ValidationError) -> str:
    lines = [f"invalid configuration in {path}:"]
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        lines.append(f"  - {location}: {error['msg']}")
    return "\n".join(lines)


def load_config(
    explicit_path: str | os.PathLike[str] | None = None,
    env: dict[str, str] | None = None,
    apply_dotenv: bool = True,
) -> Config:
    """Load and validate the server configuration."""
    if env is None:
        if apply_dotenv:
            load_dotenv(SERVER_DIR / ".env", override=False)
        env = dict(os.environ)

    path = resolve_config_path(explicit_path, env)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"invalid configuration in {path}: top level must be a mapping")

    data = _deep_merge(raw, env_overrides(env))
    try:
        return Config.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(path, exc)) from exc
