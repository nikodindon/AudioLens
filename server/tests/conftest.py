from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from app.config import Config, load_config

TOKEN = "test_token_0123456789abcdef"


def config_dict(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "server": {"token": TOKEN, "host": "127.0.0.1", "port": 8765},
        "whisper": {
            "model": "small",
            "device": "cpu",
            "compute_type": "int8",
            "allowed_models": ["tiny", "base", "small"],
        },
        "translation": {"engine": "none", "device": "cpu"},
    }
    for section, values in overrides.items():
        if isinstance(values, dict):
            data.setdefault(section, {}).update(values)
        else:
            data[section] = values
    return data


def write_config(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


@pytest.fixture
def config(tmp_path: Path) -> Config:
    path = write_config(tmp_path, config_dict())
    return load_config(path, env={}, apply_dotenv=False)


@pytest.fixture
def config_factory(tmp_path: Path):
    def factory(**overrides: Any) -> Config:
        path = write_config(tmp_path, config_dict(**overrides))
        return load_config(path, env={}, apply_dotenv=False)

    return factory
