from __future__ import annotations

from pathlib import Path

import pytest
from app.config import Config, ConfigError, env_overrides, load_config

from .conftest import TOKEN, config_dict, write_config


def test_load_minimal_config(tmp_path: Path) -> None:
    path = write_config(tmp_path, config_dict())
    config = load_config(path, env={}, apply_dotenv=False)
    assert config.server.port == 8765
    assert config.whisper.model == "small"
    assert config.whisper.compute_type == "int8"
    assert config.token == TOKEN
    assert config.vad.max_segment_s == 12.0


def test_example_config_is_valid_once_token_is_set(tmp_path: Path) -> None:
    example = Path(__file__).resolve().parents[1] / "config.example.yaml"
    with pytest.raises(ConfigError, match="placeholder"):
        load_config(example, env={}, apply_dotenv=False)

    data = config_dict()
    data["server"]["token"] = "a" * 32
    path = write_config(tmp_path, data)
    config = load_config(path, env={}, apply_dotenv=False)
    assert config.whisper.allowed_models == ["tiny", "base", "small"]


def test_environment_overrides_file(tmp_path: Path) -> None:
    path = write_config(tmp_path, config_dict())
    config = load_config(
        path,
        env={
            "AUDIOLENS_WHISPER__MODEL": "base",
            "AUDIOLENS_SERVER__PORT": "9100",
            "AUDIOLENS_VAD__ENABLED": "false",
        },
        apply_dotenv=False,
    )
    assert config.whisper.model == "base"
    assert config.server.port == 9100
    assert config.vad.enabled is False
    assert config.whisper.device == "cpu"  # untouched values survive


def test_env_overrides_returns_nested_structure() -> None:
    assert env_overrides({"AUDIOLENS_WHISPER__MODEL": "medium"}) == {"whisper": {"model": "medium"}}
    assert env_overrides({"OTHER_VAR": "x"}) == {}


def test_unknown_env_shape_is_rejected() -> None:
    with pytest.raises(ConfigError, match="unsupported environment variable"):
        env_overrides({"AUDIOLENS_WHISPER_MODEL": "tiny"})


def test_missing_token_is_rejected(tmp_path: Path) -> None:
    data = config_dict()
    del data["server"]["token"]
    path = write_config(tmp_path, data)
    with pytest.raises(ConfigError, match=r"server\.token"):
        load_config(path, env={}, apply_dotenv=False)


def test_short_token_is_rejected(tmp_path: Path) -> None:
    path = write_config(tmp_path, config_dict(server={"token": "short"}))
    with pytest.raises(ConfigError, match="at least 16 characters"):
        load_config(path, env={}, apply_dotenv=False)


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    data = config_dict()
    data["whisper"]["temperature"] = 0.2
    path = write_config(tmp_path, data)
    with pytest.raises(ConfigError, match=r"whisper\.temperature"):
        load_config(path, env={}, apply_dotenv=False)


def test_invalid_compute_type_is_rejected(tmp_path: Path) -> None:
    path = write_config(tmp_path, config_dict(whisper={"compute_type": "int4"}))
    with pytest.raises(ConfigError, match="compute_type"):
        load_config(path, env={}, apply_dotenv=False)


def test_empty_allowed_models_is_rejected(tmp_path: Path) -> None:
    path = write_config(tmp_path, config_dict(whisper={"allowed_models": []}))
    with pytest.raises(ConfigError, match="allowed_models"):
        load_config(path, env={}, apply_dotenv=False)


def test_missing_file_has_actionable_message(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=r"cp config\.example\.yaml"):
        load_config(tmp_path / "nope.yaml", env={}, apply_dotenv=False)


def test_safe_dict_masks_token(config: Config) -> None:
    safe = config.safe_dict()
    assert safe["server"]["token"] == "***"
    assert TOKEN not in str(safe)


def test_safe_dict_masks_translation_key(config_factory) -> None:
    config = config_factory(translation={"openai_api_key": "example-private-key"})
    safe = config.safe_dict()
    assert safe["translation"]["openai_api_key"] == "***"
    assert "example-private-key" not in str(safe)
    assert config.translation.openai_api_key == "example-private-key"


def test_safe_dict_hides_translation_endpoint_credentials(config_factory) -> None:
    config = config_factory(
        translation={"openai_base_url": "https://user:secret@example.com/v1?key=secret"}
    )
    assert "secret" not in str(config.safe_dict())
